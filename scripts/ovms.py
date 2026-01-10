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

# --- DECODING TABLE ---
# Maps ID -> (Name, Decoder Function)
def decode_temp(dat):
    return f"{dat[4]/2.0:.1f} C" if len(dat)>4 else "Err"

def decode_speed(dat):
    if len(dat) < 5: return "Err"
    val = (dat[4] << 8) + dat[3]
    speed = (val - 1) / 190.0
    return f"{speed:.1f} km/h"

def decode_doors(dat):
    # Standard VAG Door Bitmask (approx)
    if len(dat) < 2: return "Err"
    val = dat[0]
    doors = []
    if val & 0x01: doors.append("FL")
    if val & 0x02: doors.append("FR")
    if val & 0x04: doors.append("RL")
    if val & 0x08: doors.append("RR")
    if val & 0x10: doors.append("Trunk")
    return ",".join(doors) if doors else "Closed"

KNOWN_IDS = {
    0x52D: ("Climate Knob", decode_temp),
    0x320: ("Speedometer ", decode_speed),
    0x380: ("Door Status ", decode_doors),
    0x575: ("Key Pos/Blow", lambda d: f"Key:{d[3] if len(d)>3 else '?'} Fan:{d[0]}"),
    0x69D: ("My Heartbeat", lambda d: "Active"),
    0x5A7: ("My Status   ", lambda d: f"State:{d[0]:02x}"),
}

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
transition_active = False
fas_counter = 0
bus_awake = False

# Store data for display: {id: {'data': bytes, 'time': float}}
data_store = {}
data_lock = threading.Lock()

def perform_wakeup_handshake(p):
    """Sends the 3-step wake-up sequence."""
    try:
        # 1. Poke
        p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)
        time.sleep(0.02)
        # 2. Ring Call
        p.can_send(ID_RING, struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        time.sleep(0.06)
        # 3. Register
        p.can_send(ID_RING, struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        return True
    except:
        return False

def bg_worker(p):
    """Handles Heartbeat TX and CAN RX in background."""
    global ac_request, fas_counter, transition_active, bus_awake

    last_tick = 0

    while keep_running:
        current_time = time.time()

        # --- RX Handling ---
        incoming = p.can_recv()
        with data_lock:
            for addr, dat, bus in incoming:
                if bus == TARGET_BUS:
                    data_store[addr] = {'data': dat, 'time': current_time}
                    bus_awake = True # Traffic seen

        # --- TX Heartbeat (1Hz) ---
        if current_time - last_tick > 1.0:
            last_tick = current_time
            if bus_awake: # Only talk if bus is awake
                try:
                    p.can_send(ID_HEARTBEAT_1, b'\x00'*8, TARGET_BUS)

                    if transition_active:
                        fas_counter += 1
                        if fas_counter >= 10:
                            transition_active = False
                            fas_counter = 0

                    b0 = 0x60 if transition_active else 0x00
                    msg_5a7 = struct.pack("BBBBBBBB", b0, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
                    p.can_send(ID_HEARTBEAT_2, msg_5a7, TARGET_BUS)
                except: pass

        # --- TX AC Command ---
        if ac_request is not None:
            transition_active = True
            fas_counter = 0

            # Default to 21C, or use knob temp if available
            target_temp = 21.0
            with data_lock:
                if 0x52D in data_store and len(data_store[0x52D]['data']) >= 5:
                    target_temp = data_store[0x52D]['data'][4] / 2.0
            if target_temp < 15: target_temp = 21.0

            if ac_request == "START":
                cmd = struct.pack("BBBBBBBB", 0x80, 0x04, int(target_temp*2), 0x00, 0x00, 0x00, 0x00, 0x00)
            else:
                cmd = struct.pack("BBBBBBBB", 0x40, 0x04, int(21.0*2), 0x00, 0x00, 0x00, 0x00, 0x00)

            for _ in range(5):
                try: p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
                except: pass
                time.sleep(0.1)
            ac_request = None

        time.sleep(0.01)

def draw_dashboard(stdscr, p):
    global ac_request, bus_awake

    curses.curs_set(0)  # Hide cursor
    stdscr.nodelay(True) # Non-blocking input
    stdscr.timeout(100)  # Refresh every 100ms

    # Initial Wake
    perform_wakeup_handshake(p)

    while keep_running:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        # --- HEADER ---
        status = "AWAKE " if bus_awake else "ASLEEP"
        stdscr.addstr(0, 0, f"VW e-UP COMFORT CAN | Status: {status}", curses.A_BOLD)
        stdscr.addstr(1, 0, "-" * (w-1))

        # --- CONTROLS ---
        stdscr.addstr(2, 0, "[1] AC ON  [0] AC OFF  [w] Wake Handshake  [q] Quit")

        # --- KNOWN SIGNALS ---
        row = 4
        stdscr.addstr(row, 0, f"{'NAME':<15} {'VALUE':<15} {'LAST UPDATE':<15} {'RAW HEX'}", curses.A_UNDERLINE)
        row += 1

        current_time = time.time()

        with data_lock:
            # Sort IDs to keep list stable
            all_ids = sorted(data_store.keys())

            # 1. Print Known IDs first
            for can_id in all_ids:
                if can_id in KNOWN_IDS:
                    info = data_store[can_id]
                    age = (current_time - info['time']) * 1000
                    raw_hex = info['data'].hex()

                    name, decoder = KNOWN_IDS[can_id]
                    try:
                        value_str = decoder(info['data'])
                    except:
                        value_str = "Dec Err"

                    # Highlight fresh data
                    style = curses.A_BOLD if age < 200 else curses.A_DIM

                    line = f"{name:<15} {value_str:<15} {age:6.0f} ms       {raw_hex}"
                    stdscr.addstr(row, 0, line, style)
                    row += 1

            # 2. Print Unknown IDs (Scrollable area logic omitted for simplicity, showing first 15)
            row += 1
            stdscr.addstr(row, 0, "--- OTHER TRAFFIC ---", curses.A_DIM)
            row += 1

            for can_id in all_ids:
                if can_id not in KNOWN_IDS:
                    if row >= h - 1: break # Don't crash screen
                    info = data_store[can_id]
                    age = (current_time - info['time']) * 1000
                    raw_hex = info['data'].hex()

                    line = f"ID {hex(can_id):<12} {'':<15} {age:6.0f} ms       {raw_hex}"
                    stdscr.addstr(row, 0, line)
                    row += 1

        # --- INPUT HANDLING ---
        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('1'):
            ac_request = "START"
            stdscr.addstr(2, 40, " SENDING ON... ", curses.A_REVERSE)
        elif key == ord('0'):
            ac_request = "STOP"
            stdscr.addstr(2, 40, " SENDING OFF... ", curses.A_REVERSE)
        elif key == ord('w'):
            perform_wakeup_handshake(p)
            stdscr.addstr(2, 40, " WAKING UP... ", curses.A_REVERSE)

        stdscr.refresh()

def main():
    global keep_running
    try:
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        # Start Background Thread
        t = threading.Thread(target=bg_worker, args=(p,))
        t.start()

        # Start UI
        curses.wrapper(draw_dashboard, p)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        keep_running = False
        try: p.set_safety_mode(Panda.SAFETY_SILENT)
        except: pass

if __name__ == "__main__":
    main()