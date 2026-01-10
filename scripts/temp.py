import time
import struct
import threading
import sys
from datetime import datetime
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

# IDs
WAKE_ID    = 0x69D     # OCU Heartbeat (Keeps bus awake)
CMD_ID     = 0x69E     # Remote AC Command (Start/Stop)
READ_ID    = 0x52D     # Manual Temp Knob Feedback (Found via Sniffer)

# Global flags
keep_running = True
ac_request   = None    # None, "START", or "STOP"
current_temp_display = 0.0 # To store the last read temp

def get_wake_message():
    """Generates the OCU Heartbeat"""
    now = datetime.now()
    return struct.pack("BBBBBBBB", 0x24, now.year % 100, now.month, now.day, now.hour, now.minute, now.second, 0x00)

def get_climate_command(enable=True, temp_c=21.0):
    """
    Generates the Remote Climate Command (0x69E).
    """
    cmd_byte = 0x80 if enable else 0x40
    temp_raw = int(temp_c * 2)
    return struct.pack("BBBBBBBB", cmd_byte, 0x04, temp_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

def connection_thread(p):
    """Background thread to handle Heartbeats, Commands, and Reading"""
    global ac_request, current_temp_display

    last_wake = 0
    wake_interval = 1.0

    print("[*] Background thread started.")

    while keep_running:
        current_time = time.time()

        # 1. SEND HEARTBEAT (Every 1s)
        if current_time - last_wake > wake_interval:
            try:
                p.can_send(WAKE_ID, get_wake_message(), TARGET_BUS)
                last_wake = current_time
            except Exception:
                pass # Ignore occasional TX errors

        # 2. READ TEMP (0x52D)
        # We peek at the buffer here to update the global variable
        incoming = p.can_recv()
        for addr, dat, bus in incoming:
            if bus == TARGET_BUS and addr == READ_ID:
                # Based on your sniffer: 0x52D, Byte 4 is the temp
                if len(dat) >= 5:
                    raw_val = dat[4]
                    current_temp_display = raw_val / 2.0

        # 3. HANDLE AC REQUESTS (Burst send)
        if ac_request == "START":
            # Use the currently set manual temp as the target, or default to 21
            target = current_temp_display if current_temp_display > 10 else 21.0
            print(f" -> TX: Sending AC ON (Target: {target}°C)...")

            cmd = get_climate_command(enable=True, temp_c=target)
            for _ in range(5): # Burst 5 times
                p.can_send(CMD_ID, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

        elif ac_request == "STOP":
            print(" -> TX: Sending AC OFF...")
            cmd = get_climate_command(enable=False, temp_c=21.0) # Temp doesn't matter for OFF
            for _ in range(5):
                p.can_send(CMD_ID, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

        time.sleep(0.01)

def main():
    global keep_running, ac_request

    print("[*] Initializing Panda...")
    try:
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
    except Exception as e:
        print(f"[!] Error: {e}")
        return

    t = threading.Thread(target=connection_thread, args=(p,))
    t.start()

    print("\n--- CONTROLS ---")
    print(" 1 : Turn AC ON")
    print(" 0 : Turn AC OFF")
    print(" q : Quit")
    print("----------------")
    print("Waiting for temp data...")

    try:
        while True:
            # Simple UI Loop
            # We use input() which blocks, so the update only happens after you press Enter
            # In a real app, this would be non-blocking.

            # Print status before asking for input
            print(f"\r [Status] Dashboard Setpoint: {current_temp_display:.1f}°C ", end="")

            user_input = input("\n Cmd > ").strip().lower()

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