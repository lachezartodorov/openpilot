import time
import struct
import threading
import curses
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2
BUS_SPEED  = 500       # 100kbps

# --- IDs ---
ID_POKE       = 0x69E
ID_RING       = 0x43D
ID_HEARTBEAT_1= 0x5A9
ID_HEARTBEAT_2= 0x5A7
ID_CMD_RW     = 0x69E  # Used for both AC Command AND Profile Read/Write

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
transition_active = False
fas_counter = 0
bus_awake = False
data_store = {}
data_lock = threading.Lock()

# Profile Data (43 bytes buffer, initialized to zeros)
# We need this to preserve other settings (like charge current) when writing temp
profile_data = [0] * 43
profile_loaded = False

# --- DECODERS ---
def decode_temp_knob(dat):
    if len(dat) < 5: return "Err"
    raw = dat[4]
    if raw == 0xFF or raw == 0xFE: return "N/A (Off)"
    return f"{raw/2.0:.1f} C"

def decode_profile_temp(raw_val):
    # OVMS Formula: (Raw / 10) + 10
    return (raw_val / 10.0) + 10.0

def encode_profile_temp(target_c):
    # OVMS Formula: (Target - 10) * 10
    # Limits: 10C to 35C roughly
    if target_c < 10: target_c = 10
    if target_c > 35: target_c = 35
    return int((target_c - 10) * 10)

def decode_vin(dat):
    try: return dat[1:].decode('ascii', errors='ignore')
    except: return "..."

# --- PROFILE MANAGER ---
def request_profile_read(p):
    """Sends the command to ask car for Profile 0"""
    # 90 04 19 59 27 00 00 01 (Read PID 959)
    print(" [TX] Requesting Profile Read...")
    msg = struct.pack("BBBBBBBB", 0x90, 0x04, 0x19, 0x59, 0x27, 0x00, 0x00, 0x01)
    p.can_send(ID_CMD_RW, msg, TARGET_BUS)

def write_profile_temp(p, target_temp):
    """Writes the full profile back to car with new Temp"""
    global profile_data, profile_loaded

    if not profile_loaded:
        return False, "No Profile Loaded! Read first ('r')"

    print(f" [TX] Writing Profile with Temp {target_temp}C...")

    # 1. Update Temperature Byte (Index 25)
    new_raw = encode_profile_temp(target_temp)
    profile_data[25] = new_raw

    # 2. Header Frame (Setup Write)
    # 90 20 29 59 2c 00 00 01
    hdr = struct.pack("BBBBBBBB", 0x90, 0x20, 0x29, 0x59, 0x2C, 0x00, 0x00, 0x01)
    p.can_send(ID_CMD_RW, hdr, TARGET_BUS)
    time.sleep(0.02) # Small delay for car to process header

    # 3. Data Frame D0 (Bytes 11-18)
    # Structure: D0 [11] [12] [13] [14] [15] [17] [18] -> Note: OVMS skips 16?
    # OVMS Code: data[1]=profile[11], data[2]=profile[12], data[3]=profile[13]...
    # Wait, OVMS code skips index 16 in D0 pack. Let's follow OVMS exactly.
    # D0 Packet: D0, P[11], P[12], P[13], P[14], P[15], P[17], P[18]
    d0 = struct.pack("BBBBBBBB", 0xD0, profile_data[11], profile_data[12], profile_data[13],
                     profile_data[14], profile_data[15], profile_data[17], profile_data[18])
    p.can_send(ID_CMD_RW, d0, TARGET_BUS)
    time.sleep(0.01)

    # 4. Data Frame D1 (Bytes 19-26) -> Contains TEMP at Byte 6 (Index 25)
    # D1 Packet: D1, P[19], P[20], P[21], P[22], P[23], P[25], P[26] (Skipped 24?)
    # OVMS: data[6] = (key==TEMP) ? val : profile0[25]
    d1 = struct.pack("BBBBBBBB", 0xD1, profile_data[19], profile_data[20], profile_data[21],
                     profile_data[22], profile_data[23], profile_data[25], profile_data[26])
    p.can_send(ID_CMD_RW, d1, TARGET_BUS)
    time.sleep(0.01)

    # 5. Data Frame D2 (Bytes 27-34)
    # D2 Packet: D2, P[27], P[28], P[29], P[30], P[31], P[33], P[34] (Skipped 32?)
    d2 = struct.pack("BBBBBBBB", 0xD2, profile_data[27], profile_data[28], profile_data[29],
                     profile_data[30], profile_data[31], profile_data[33], profile_data[34])
    p.can_send(ID_CMD_RW, d2, TARGET_BUS)
    time.sleep(0.01)

    # 6. Data Frame D3 (Bytes 35-42)
    # D3 Packet: D3, P[35], P[36], P[37], P[38], P[39], P[41], P[42]
    d3 = struct.pack("BBBBBBBB", 0xD3, profile_data[35], profile_data[36], profile_data[37],
                     profile_data[38], profile_data[39], profile_data[41], profile_data[42])
    p.can_send(ID_CMD_RW, d3, TARGET_BUS)

    return True, "Write Sent"

# --- WORKER THREAD ---
def bg_worker(p):
    global ac_request, fas_counter, transition_active, bus_awake, profile_data, profile_loaded
    last_tick = 0

    # We need to detect Profile Responses (D0, D1...)
    # We assume the car replies on the same bus, likely on ID 0x69E or similar

    while keep_running:
        current_time = time.time()

        # --- RX Handling ---
        incoming = p.can_recv()
        with data_lock:
            for addr, dat, bus in incoming:
                if bus == TARGET_BUS:
                    data_store[addr] = {'data': dat, 'time': current_time}
                    bus_awake = True

                    # CAPTURE PROFILE DATA (Simple Sniffer)
                    # If we see D0/D1/D2/D3 headers on 0x69E (or return ID), we capture
                    if len(dat) == 8:
                        header = dat[0]
                        # This logic assumes the car broadcasts the read response on the bus
                        # We might need to adjust 'addr' if the car replies on a different ID
                        if header == 0xD0:
                            # Capture P[11]..P[18]
                            # Mapping based on Write logic:
                            # Dat: [D0] [11] [12] [13] [14] [15] [17] [18]
                            profile_data[11:19] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                        elif header == 0xD1:
                            # Dat: [D1] [19] [20] [21] [22] [23] [25] [26]
                            profile_data[19:27] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                            profile_loaded = True # We have the temp chunk at least
                        elif header == 0xD2:
                             profile_data[27:35] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                        elif header == 0xD3:
                             profile_data[35:43] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]

        # --- TX Heartbeat (1Hz) ---
        if current_time - last_tick > 1.0:
            last_tick = current_time
            if bus_awake:
                try:
                    p.can_send(ID_HEARTBEAT_1, b'\x00'*8, TARGET_BUS)
                    if transition_active:
                        fas_counter += 1
                        if fas_counter >= 10:
                            transition_active = False
                            fas_counter = 0
                    b0 = 0x60 if transition_active else 0x00
                    p.can_send(ID_HEARTBEAT_2, struct.pack("8B", b0, 0x16, 0,0,0,0,0,0), TARGET_BUS)
                except: pass

        # --- TX AC Command (Start/Stop) ---
        if ac_request is not None:
            transition_active = True
            fas_counter = 0

            # This is the "Turn On" command, not the "Configure" command
            if ac_request == "START":
                # 80 04 ... (Start AC)
                # Note: This command uses the temp configured in the Profile!
                cmd = struct.pack("BBBBBBBB", 0x80, 0x04, 0x00, 0,0,0,0,0)
                # OVMS sends 0x00 for temp bytes here because it relies on the pre-configured profile
            else:
                cmd = struct.pack("BBBBBBBB", 0x40, 0x04, 0x00, 0,0,0,0,0)

            for _ in range(5):
                try: p.can_send(ID_CMD_RW, cmd, TARGET_BUS)
                except: pass
                time.sleep(0.1)
            ac_request = None

        time.sleep(0.01)

def perform_wakeup_handshake(p):
    try:
        p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)
        time.sleep(0.02)
        p.can_send(ID_RING, struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        time.sleep(0.06)
        p.can_send(ID_RING, struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        return True
    except: return False

def draw_dashboard(stdscr, p):
    global ac_request, bus_awake, profile_data, profile_loaded
    curses.curs_set(0); stdscr.nodelay(True); stdscr.timeout(100)
    perform_wakeup_handshake(p)

    msg_status = ""

    while keep_running:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        status = "AWAKE" if bus_awake else "SLEEP"
        stdscr.addstr(0, 0, f"VW e-UP COMMANDER | Status: {status} | {msg_status}", curses.A_BOLD)
        stdscr.addstr(1, 0, "-"*w)

        # Profile Info
        p_temp_c = decode_profile_temp(profile_data[25])
        p_curr_a = profile_data[13]
        p_status = "LOADED" if profile_loaded else "EMPTY (Press 'r')"

        stdscr.addstr(2, 0, f"PROFILE: {p_status} | Target Temp: {p_temp_c:.1f}C | Limit: {p_curr_a}A")
        stdscr.addstr(3, 0, "-"*w)
        stdscr.addstr(4, 0, "[1] AC ON    [0] AC OFF")
        stdscr.addstr(5, 0, "[r] READ Profile   [t] WRITE Temp (22C)")
        stdscr.addstr(6, 0, "[q] Quit")

        # Live Data
        row = 8
        stdscr.addstr(row, 0, f"{'SENSOR':<15} {'VALUE':<15}", curses.A_UNDERLINE)
        row += 1

        with data_lock:
            # Outdoor
            if 0x5DC in data_store:
                 d = data_store[0x5DC]['data']
                 val = f"{(d[0]-110)/2.0:.1f} C" if len(d)>0 else "?"
                 stdscr.addstr(row, 0, f"{'Outdoor':<15} {val:<15}"); row+=1

            # Knob
            if 0x52D in data_store:
                 d = data_store[0x52D]['data']
                 val = decode_temp_knob(d)
                 stdscr.addstr(row, 0, f"{'Knob Temp':<15} {val:<15}"); row+=1

            # Odo
            if 0x520 in data_store:
                 d = data_store[0x520]['data']
                 if len(d)>=8:
                     val = (d[7]<<16)+(d[6]<<8)+d[5]
                     stdscr.addstr(row, 0, f"{'Odometer':<15} {val} km"); row+=1

        # Input
        key = stdscr.getch()
        if key == ord('q'): break
        elif key == ord('1'):
            ac_request = "START"
            msg_status = "Sending AC START..."
        elif key == ord('0'):
            ac_request = "STOP"
            msg_status = "Sending AC STOP..."
        elif key == ord('r'):
            request_profile_read(p)
            msg_status = "Requesting Profile..."
        elif key == ord('t'):
            if profile_loaded:
                # Toggle between 21 and 24 for demo
                curr = decode_profile_temp(profile_data[25])
                target = 24.0 if curr < 23 else 21.0
                res, txt = write_profile_temp(p, target)
                msg_status = f"Set {target}C: {txt}"
            else:
                msg_status = "Error: Read profile first!"
        elif key == ord('w'):
            perform_wakeup_handshake(p)
            msg_status = "Waking up..."

        stdscr.refresh()

def main():
    global keep_running
    try:
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
        t = threading.Thread(target=bg_worker, args=(p,))
        t.start()
        curses.wrapper(draw_dashboard, p)
    except Exception as e: print(e)
    finally:
        keep_running = False
        try: p.set_safety_mode(Panda.SAFETY_SILENT)
        except: pass

if __name__ == "__main__":
    main()