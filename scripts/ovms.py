import time
import struct
import threading
import curses
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 1         # 0=CAN1, 1=CAN2
BUS_SPEED  = 100       # 100kbps

# --- IDs ---
ID_POKE       = 0x69E
ID_RING       = 0x43D
ID_HEARTBEAT_1= 0x5A9
ID_HEARTBEAT_2= 0x5A7
ID_AC_CMD     = 0x69E

# --- DECODERS ---
def decode_temp_knob(dat):
    if len(dat) < 5: return "Err"
    raw = dat[4]
    if raw == 0xFF or raw == 0xFE: return "N/A (Off)"
    return f"{raw/2.0:.1f} C"

def decode_vin(dat):
    # 0x5D2: 02 [44 39...] -> ASCII
    try: return dat[1:].decode('ascii', errors='ignore')
    except: return "..."

def decode_odo_try1(dat):
    # 0x520/0x621: Candidate A (Big Endian)
    # [00 2d 77] -> 0x2D77 = 11639
    if len(dat) < 4: return "Err"
    val = (dat[1] << 8) + dat[2]
    return f"{val} km?"

def decode_odo_try2(dat):
    # 0x520/0x621: Candidate B (Little Endian)
    # [00 2d 77] -> 0x772D = 30509
    if len(dat) < 4: return "Err"
    val = (dat[2] << 8) + dat[1]
    return f"{val} km?"

def decode_range(dat):
    # 0x658 Byte 5: [60 28 00 09 00 9b ...] -> 9b = 155
    if len(dat) < 6: return "Err"
    return f"{dat[5]} km"

def decode_soc(dat):
    # 0x62B Byte 4
    if len(dat) < 5: return "Err"
    return f"{dat[4]} %"

def decode_handbrake(dat):
    # 0x390 Byte 0 Bit 0
    if len(dat) < 1: return "Err"
    return "ON" if (dat[0] & 1) else "OFF"

def decode_out_temp(dat):
    if len(dat) < 1: return "Err"
    return f"{(dat[0] - 100) / 2.0:.1f} C"

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
    0x658: ("Est. Range  ", decode_range),     # NEW
    0x390: ("Handbrake   ", decode_handbrake), # NEW
    0x5DC: ("Outdoor Temp", decode_out_temp),
    0x380: ("Doors       ", decode_doors),
    0x320: ("Speedometer ", lambda d: f"{( ((d[4]<<8)+d[3]) -1)/190:.1f} km/h" if len(d)>4 else "Err"),
    # Odometer Experiments
    0x621: ("Odo (Guess A)", decode_odo_try1),
    0x520: ("Odo (Guess B)", decode_odo_try2),
}

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
transition_active = False
fas_counter = 0
bus_awake = False
data_store = {}
data_lock = threading.Lock()

def perform_wakeup_handshake(p):
    try:
        p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)
        time.sleep(0.02)
        p.can_send(ID_RING, struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        time.sleep(0.06)
        p.can_send(ID_RING, struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        return True
    except: return False

def bg_worker(p):
    global ac_request, fas_counter, transition_active, bus_awake
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

        # TX Heartbeat (1Hz)
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
            target = 21.0
            with data_lock:
                if 0x52D in data_store and len(data_store[0x52D]['data']) > 4:
                    val = data_store[0x52D]['data'][4]
                    if val != 0xFF and val != 0xFE: target = val / 2.0

            if target < 15: target = 21.0

            if ac_request == "START":
                cmd = struct.pack("BBBBBBBB", 0x80, 0x04, int(target*2), 0,0,0,0,0)
            else:
                cmd = struct.pack("BBBBBBBB", 0x40, 0x04, int(21*2), 0,0,0,0,0)

            for _ in range(5):
                try: p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
                except: pass
                time.sleep(0.1)
            ac_request = None

        time.sleep(0.01)

def draw_dashboard(stdscr, p):
    global ac_request, bus_awake
    curses.curs_set(0); stdscr.nodelay(True); stdscr.timeout(100)
    perform_wakeup_handshake(p)

    while keep_running:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        status = "AWAKE" if bus_awake else "SLEEP"
        stdscr.addstr(0, 0, f"VW e-UP DASHBOARD | Status: {status} | Press 'w' to wake", curses.A_BOLD)
        stdscr.addstr(1, 0, "-"*w)
        stdscr.addstr(2, 0, "[1] AC ON   [0] AC OFF   [q] Quit")

        row = 4
        stdscr.addstr(row, 0, f"{'METRIC':<15} {'VALUE':<15} {'LAST UPDATE':<15} {'RAW HEX'}", curses.A_UNDERLINE)
        row += 1

        curr = time.time()
        with data_lock:
            # Show Known
            for cid in sorted(KNOWN_IDS.keys()):
                if cid in data_store:
                    info = data_store[cid]
                    age = (curr - info['time']) * 1000
                    name, func = KNOWN_IDS[cid]
                    try: val_str = func(info['data'])
                    except: val_str = "Dec Err"

                    style = curses.A_BOLD if age < 500 else curses.A_DIM
                    stdscr.addstr(row, 0, f"{name:<15} {val_str:<15} {age:6.0f} ms       {info['data'].hex()}", style)
                    row += 1

            # Show Unknown (limit to space)
            row += 1; stdscr.addstr(row, 0, "--- RAW TRAFFIC ---", curses.A_DIM); row += 1
            for cid in sorted(data_store.keys()):
                if cid not in KNOWN_IDS and row < h-1:
                    info = data_store[cid]
                    age = (curr - info['time']) * 1000
                    stdscr.addstr(row, 0, f"ID {hex(cid):<12} {'':<15} {age:6.0f} ms       {info['data'].hex()}")
                    row += 1

        key = stdscr.getch()
        if key == ord('q'): break
        elif key == ord('1'): ac_request = "START"
        elif key == ord('0'): ac_request = "STOP"
        elif key == ord('w'): perform_wakeup_handshake(p)

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