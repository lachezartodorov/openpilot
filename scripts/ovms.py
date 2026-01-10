import time
import struct
import threading
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

# --- IDs ---
ID_HEARTBEAT_1 = 0x5A9 # The "Keep Alive" (All Zeros)
ID_HEARTBEAT_2 = 0x5A7 # The "Status" (00 16 ... or 60 16 ...)
ID_AC_CMD      = 0x69E # The Command (Start/Stop)
ID_KNOB_READ   = 0x52D # Your verified Temp Knob ID

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None    # "START" or "STOP"
current_temp = 21.0    # Default fallback

# Simulation State (Matches OVMS variables)
fas_counter = 0        # Counter for the 10s "Active" window
transition_active = False

def get_ac_command(enable=True, temp_c=21.0):
    """Generates 0x69E AC Command"""
    cmd_byte = 0x80 if enable else 0x40
    temp_raw = int(temp_c * 2)
    # [Cmd, 04, TempRaw, 00, 00, 00, 00, 00]
    return struct.pack("BBBBBBBB", cmd_byte, 0x04, temp_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

def connection_thread(p):
    global ac_request, current_temp, fas_counter, transition_active

    print("[*] Background thread: Emulating OVMS Heartbeat...")

    last_tick = 0

    while keep_running:
        current_time = time.time()

        # --- 1Hz HEARTBEAT CYCLE ---
        if current_time - last_tick > 1.0:
            last_tick = current_time

            # A. Send 0x5A9 (Always 00s)
            p.can_send(ID_HEARTBEAT_1, b'\x00'*8, TARGET_BUS)

            # B. Manage Transition Logic (The 10s window)
            # If we are in a transition (just turned ON or OFF), increment counter
            if transition_active:
                fas_counter += 1
                if fas_counter >= 10:
                    transition_active = False # Stop sending 0x60 after 10 ticks
                    fas_counter = 0

            # C. Determine Byte 0 for 0x5A7
            # If counting (1-9), send 0x60. Otherwise 0x00.
            b0 = 0x60 if transition_active else 0x00

            # D. Send 0x5A7: [B0, 0x16, 00...]
            msg_5a7 = struct.pack("BBBBBBBB", b0, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
            p.can_send(ID_HEARTBEAT_2, msg_5a7, TARGET_BUS)

            # Debug print only during transition so we know it's happening
            if transition_active:
                print(f" [Heartbeat] Transition Active ({fas_counter}/10) - Sending 0x60 on 5A7")

        # --- COMMAND HANDLING ---
        if ac_request is not None:
            # 1. Trigger the "Transition" state (activates 0x60 on 5A7 for 10s)
            transition_active = True
            fas_counter = 0

            # 2. Send the actual AC Command Burst
            if ac_request == "START":
                target = current_temp if current_temp > 15 else 21.0
                print(f" -> TX: AC START (Target {target}°C)")
                cmd = get_ac_command(enable=True, temp_c=target)
            else:
                print(f" -> TX: AC STOP")
                cmd = get_ac_command(enable=False, temp_c=21.0)

            # Burst command 5 times rapidly
            for _ in range(5):
                p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
                time.sleep(0.1)

            ac_request = None # Reset request

        # --- READ TEMP (0x52D) ---
        incoming = p.can_recv()
        for addr, dat, bus in incoming:
            if bus == TARGET_BUS and addr == ID_KNOB_READ:
                if len(dat) >= 5:
                    val = dat[4] / 2.0
                    if val > 0: current_temp = val

        time.sleep(0.01)

def main():
    global keep_running, ac_request

    try:
        print("[*] Initializing Panda...")
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
    except Exception as e:
        print(f"[!] Error: {e}")
        return

    t = threading.Thread(target=connection_thread, args=(p,))
    t.start()

    print("\n--- OVMS LOGIC EMULATOR ---")
    print(" 1 : Turn AC ON")
    print(" 0 : Turn AC OFF")
    print(" q : Quit")
    print("---------------------------")

    try:
        while True:
            print(f"\r [Status] Knob Temp: {current_temp:.1f}°C ", end="")
            user_input = input().strip().lower()

            if user_input == '1':
                ac_request = "START"
            elif user_input == '0':
                ac_request = "STOP"
            elif user_input == 'q':
                break

    except KeyboardInterrupt:
        pass
    finally:
        keep_running = False
        t.join()
        p.set_safety_mode(Panda.SAFETY_SILENT)
        print("\n[*] Exited.")

if __name__ == "__main__":
    main()