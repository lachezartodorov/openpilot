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
ID_AC_CMD     = 0x69E
ID_PROFILE_RW = 0x69E

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
transition_active = False
fas_counter = 0
bus_awake = False
data_store = {}
data_lock = threading.Lock()

# Profile Data (43 bytes buffer)
profile_data = [0] * 43
profile_loaded = False

# --- DECODERS ---
def decode_temp_knob(dat):
    if len(dat) < 5: return "Err"
    raw = dat[4]
    if raw == 0xFF or raw == 0xFE: return "N/A (Off)"
    return f"{raw/2.0:.1f} C"

def decode_profile_temp(raw_val):
    return (raw_val / 10.0) + 10.0

def encode_profile_temp(target_c):
    if target_c < 10: target_c = 10
    if target_c > 35: target_c = 35
    return int((target_c - 10) * 10)

def decode_vin(dat):
    try: return dat[1:].decode('ascii', errors='ignore')
    except: return "..."

def decode_odo_fixed(dat):
    if len(dat) < 8: return "Err"
    val = (dat[7] << 16) + (dat[6] << 8) + dat[5]
    return f"{val} km"

def decode_range(dat):
    if len(dat) < 6: return "Err"
    return f"{dat[5]} km (Std)"

def decode_soc(dat):
    if len(dat) < 5: return "Err"
    return f"{dat[4]} %"

def decode_handbrake(dat):
    if len(dat) < 1: return "Err"
    return "OFF" if dat[0] == 0x83 else "ON?"

def decode_out_temp_fixed(dat):
    if len(dat) < 1: return "Err"
    return f"{(dat[0] - 110) / 2.0:.1f} C"

def decode_doors(dat):
    if len(dat) < 1: return "Err"
    val = dat[0]
    doors = []
    if val & 0x01: doors.append("FL")
    if val & 0x02: doors.append("FR")
    if val & 0x04: doors.append("RL")
    if val & 0x08: doors.append("RR")
    if val & 0x10: doors.append("Trunk")
    return " ".join(doors) if doors else "All Closed"

# --- MAPPING ---
KNOWN_IDS = {
    0x52D: ("Climate Knob", decode_temp_knob),
    0x5D2: ("VIN (End)   ", decode_vin),
    0x62B: ("Battery SoC ", decode_soc),
    0x658: ("Rated Range ", decode_range),
    0x390: ("Handbrake   ", decode_handbrake),
    0x5DC: ("Outdoor Temp", decode_out_temp_fixed),
    0x380: ("Doors       ", decode_doors),
    0x520: ("Odometer    ", decode_odo_fixed),
    0x320: ("Speedometer ", lambda d: f"{( ((d[4]<<8)+d[3]) -1)/190:.1f} km/h" if len(d)>4 else "Err"),
}

# --- ROBUST REQUEST ---
def request_profile_read(p):
    """Retries sending request until successful"""
    msg = struct.pack("BBBBBBBB", 0x90, 0x04, 0x19, 0x59, 0x27, 0x00, 0x00, 0x01)

    # Send up to 5 times or until we see it
    for i in range(5):
        try:
            p.can_send(ID_PROFILE_RW, msg, TARGET_BUS)
            time.sleep(0.05) # Small wait
            return True, "Request Sent"
        except Exception as e:
            time.sleep(0.1)
    return False, "TX Failed"

def write_profile_temp(p, target_temp):
    global profile_data, profile_loaded
    if not profile_loaded: return False, "No Profile Loaded!"

    new_raw = encode_profile_temp(target_temp)
    profile_data[25] = new_raw

    # 1. Header
    hdr = struct.pack("BBBBBBBB", 0x90, 0x20, 0x29, 0x59, 0x2C, 0x00, 0x00, 0x01)
    p.can_send(ID_PROFILE_RW, hdr, TARGET_BUS)
    time.sleep(0.02)

    # 2. D0
    d0 = struct.pack("BBBBBBBB", 0xD0, profile_data[11], profile_data[12], profile_data[13],
                     profile_data[14], profile_data[15], profile_data[17], profile_data[18])
    p.can_send(ID_PROFILE_RW, d0, TARGET_BUS)
    time.sleep(0.01)

    # 3. D1 (Has Temp at Byte 6)
    d1 = struct.pack("BBBBBBBB", 0xD1, profile_data[19], profile_data[20], profile_data[21],
                     profile_data[22], profile_data[23], profile_data[25], profile_data[26])
    p.can_send(ID_PROFILE_RW, d1, TARGET_BUS)
    time.sleep(0.01)

    # 4. D2
    d2 = struct.pack("BBBBBBBB", 0xD2, profile_data[27], profile_data[28], profile_data[29],
                     profile_data[30], profile_data[31], profile_data[33], profile_data[34])
    p.can_send(ID_PROFILE_RW, d2, TARGET_BUS)
    time.sleep(0.01)

    # 5. D3
    d3 = struct.pack("BBBBBBBB", 0xD3, profile_data[35], profile_data[36], profile_data[37],
                     profile_data[38], profile_data[39], profile_data[41], profile_data[42])
    p.can_send(ID_PROFILE_RW, d3, TARGET_BUS)

    return True, "Write Sent"

# --- WORKER ---
def bg_worker(p):
    global ac_request, fas_counter, transition_active, bus_awake, profile_data, profile_loaded
    last_tick = 0

    while keep_running:
        current_time = time.time()

        # RX
        incoming = p.can_recv()
        with data_lock:
            for addr, dat, bus in incoming:
                if bus == TARGET_BUS:
                    data_store[addr] = {'data': dat, 'time': current_time}
                    bus_awake = True

                    # CAPTURE PROFILE RESPONSE
                    # Look for D0/D1/D2/D3 headers on any ID (car might reply on 69F)
                    if len(dat) == 8 and (addr == 0x69E or addr == 0x69F or addr == 0x76E):
                        header = dat[0]
                        if header == 0xD0:
                            profile_data[11:19] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                        elif header == 0xD1:
                            profile_data[19:27] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                            profile_loaded = True
                        elif header == 0xD2:
                             profile_data[27:35] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]
                        elif header == 0xD3:
                             profile_data[35:43] = [dat[1], dat[2], dat[3], dat[4], dat[5], 0, dat[6], dat[7]]

        # TX Heartbeat
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

        # TX AC Command
        if ac_request is not None:
            transition_active = True
            fas_counter = 0

            if ac_request == "START":
                cmd = struct.pack("BBBBBBBB", 0x80, 0x04, 0x00, 0,0,0,0,0)
            else:
                cmd = struct.pack("BBBBBBBB", 0x40, 0x04, 0x00, 0,0,0,0,0)

            for _ in range(5):
                try: p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
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

        p_temp_c = decode_profile_temp(profile_data[25])
        p_curr_a = profile_data[13]
        p_status = "LOADED" if profile_loaded else "EMPTY (Press 'r')"

        stdscr.addstr(2, 0, f"PROFILE: {p_status} | Target Temp: {p_temp_c:.1f}C | Limit: {p_curr_a}A")
        stdscr.addstr(3, 0, "-"*w)
        stdscr.addstr(4, 0, "[1] AC ON    [0] AC OFF")
        stdscr.addstr(5, 0, "[r] READ Profile   [t] WRITE Temp (Toggle 21C/24C)")
        stdscr.addstr(6, 0, "[q] Quit")

        row = 8
        stdscr.addstr(row, 0, f"{'SENSOR':<15} {'VALUE':<15}", curses.A_UNDERLINE)
        row += 1

        with data_lock:
            if 0x5DC in data_store:
                 d = data_store[0x5DC]['data']
                 val = f"{(d[0]-110)/2.0:.1f} C" if len(d)>0 else "?"
                 stdscr.addstr(row, 0, f"{'Outdoor':<15} {val:<15}"); row+=1

            if 0x52D in data_store:
                 d = data_store[0x52D]['data']
                 val = decode_temp_knob(d)
                 stdscr.addstr(row, 0, f"{'Knob Temp':<15} {val:<15}"); row+=1

            if 0x520 in data_store:
                 d = data_store[0x520]['data']
                 if len(d)>=8:
                     val = (d[7]<<16)+(d[6]<<8)+d[5]
                     stdscr.addstr(row, 0, f"{'Odometer':<15} {val} km"); row+=1

        key = stdscr.getch()
        if key == ord('q'): break
        elif key == ord('1'):
            ac_request = "START"; msg_status = "Sending AC START..."
        elif key == ord('0'):
            ac_request = "STOP"; msg_status = "Sending AC STOP..."
        elif key == ord('r'):
            res, txt = request_profile_read(p)
            msg_status = txt
        elif key == ord('t'):
            if profile_loaded:
                curr = decode_profile_temp(profile_data[25])
                target = 24.0 if curr < 23 else 21.0
                res, txt = write_profile_temp(p, target)
                msg_status = f"Set {target}C: {txt}"
            else: msg_status = "Read first!"
        elif key == ord('w'):
            perform_wakeup_handshake(p); msg_status = "Waking up..."

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