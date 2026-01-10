import time
import struct
import threading
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

# --- IDs ---
ID_POKE       = 0x69E  # Wakeup Poke
ID_RING       = 0x43D  # Ring Registration
ID_HEARTBEAT_1= 0x5A9  # Keep Alive (00s)
ID_HEARTBEAT_2= 0x5A7  # Status (16/60)
ID_AC_CMD     = 0x69E  # AC Command
ID_KNOB_READ  = 0x52D  # Temp Knob (Verified)

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
current_temp = 21.0
transition_active = False
fas_counter = 0

def get_ac_command(enable=True, temp_c=21.0):
    cmd_byte = 0x80 if enable else 0x40
    temp_raw = int(temp_c * 2)
    return struct.pack("BBBBBBBB", cmd_byte, 0x04, temp_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

def perform_wakeup_handshake(p):
    """
    Implements WakeupT26Stage2 from OVMS code.
    Required to register in the VW Ring logic.
    """
    print("[-] STARTING WAKE-UP HANDSHAKE...")

    # 1. The Poke (0x69E, Len 2)
    # "Request current time (PID 451)... wakes 0x400"
    print(" -> Step 1: Poke Gateway (0x69E)")
    p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)

    # 2. The Self-Call (0x43D, Len 8)
    # "Call ourself to wake up the ring"
    # Data: 1D 02 02 00 00 14 00 00
    print(" -> Step 2: Ring Self-Call (0x43D)")
    msg_self = struct.pack("BBBBBBBB", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00)
    p.can_send(ID_RING, msg_self, TARGET_BUS)

    # 3. Wait 50ms
    time.sleep(0.05)

    # 4. The Registration (0x43D, Len 8)
    # "Talk to 0x400 first to get accepted"
    # Data: 00 01 02 04 00 14 00 00
    print(" -> Step 3: Ring Registration (0x43D)")
    msg_reg = struct.pack("BBBBBBBB", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00)
    p.can_send(ID_RING, msg_reg, TARGET_BUS)

    print("[-] HANDSHAKE COMPLETE. Starting Heartbeat...")

def connection_thread(p):
    global ac_request, current_temp, fas_counter, transition_active

    last_tick = 0

    while keep_running:
        current_time = time.time()

        # --- 1Hz HEARTBEAT CYCLE (0x5A9 + 0x5A7) ---
        if current_time - last_tick > 1.0:
            last_tick = current_time

            # A. Send 0x5A9 (All Zeros)
            p.can_send(ID_HEARTBEAT_1, b'\x00'*8, TARGET_BUS)

            # B. Manage Transition Logic (10s Active Window)
            if transition_active:
                fas_counter += 1
                if fas_counter >= 10:
                    transition_active = False
                    fas_counter = 0

            # C. Send 0x5A7 (Status)
            # Normal: 00 16 ... | Active: 60 16 ...
            b0 = 0x60 if transition_active else 0x00
            msg_5a7 = struct.pack("BBBBBBBB", b0, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
            p.can_send(ID_HEARTBEAT_2, msg_5a7, TARGET_BUS)

        # --- COMMAND HANDLING ---
        if ac_request is not None:
            transition_active = True # Trigger the 0x60 status
            fas_counter = 0

            if ac_request == "START":
                # Use current knob temp (safe fallback 21C)
                target = current_temp if current_temp > 15 else 21.0
                print(f" -> TX: AC START (Target {target:.1f}°C)")
                cmd = get_ac_command(enable=True, temp_c=target)
            else:
                print(f" -> TX: AC STOP")
                cmd = get_ac_command(enable=False, temp_c=21.0)

            # Burst command 5 times
            for _ in range(5):
                p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

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

    # --- EXECUTE THE NEW HANDSHAKE ---
    perform_wakeup_handshake(p)

    # Start the background heartbeat
    t = threading.Thread(target=connection_thread, args=(p,))
    t.start()

    print("\n--- CONTROL INTERFACE ---")
    print(" 1 : Turn AC ON")
    print(" 0 : Turn AC OFF")
    print(" q : Quit")
    print("-------------------------")

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