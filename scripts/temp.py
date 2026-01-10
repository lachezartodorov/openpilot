import time
import struct
import threading
from datetime import datetime
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

# IDs
WAKE_ID    = 0x69D     # OCU Heartbeat
CMD_ID     = 0x69E     # Remote AC Command
READ_ID    = 0x52D     # Manual Temp Knob (Corrected from Sniffer)

# Global State
keep_running = True
ac_request   = None
current_temp_display = 21.0 # Default starting value

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
    """Background thread for Communication"""
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
            except:
                pass

        # 2. READ TEMP from 0x52D
        incoming = p.can_recv()
        for addr, dat, bus in incoming:
            if bus == TARGET_BUS and addr == READ_ID:
                # Log shows 0x52D has 8 bytes. Byte 4 is the temp.
                # Example: 8d 40 00 00 [2b] 00 00 10
                if len(dat) >= 5:
                    raw_val = dat[4] # Index 4 is the 5th byte
                    # Filter out zero/invalid readings if necessary
                    if raw_val > 0:
                        current_temp_display = raw_val / 2.0

        # 3. HANDLE AC REQUESTS
        if ac_request == "START":
            # Use the manual knob setting as the target temp
            target = current_temp_display if current_temp_display > 15 else 21.0
            print(f" -> TX: AC ON (Target: {target}°C)...")

            cmd = get_climate_command(enable=True, temp_c=target)
            for _ in range(5):
                p.can_send(CMD_ID, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

        elif ac_request == "STOP":
            print(" -> TX: AC OFF...")
            cmd = get_climate_command(enable=False, temp_c=21.0)
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
    print(" 1 : Turn AC ON (Uses Dashboard Temp)")
    print(" 0 : Turn AC OFF")
    print(" q : Quit")
    print("----------------")

    try:
        while True:
            # Display current temp reading constantly
            print(f"\r [Status] Knob Temp: {current_temp_display:.1f}°C   ", end="")

            # Blocking input (press Enter to send command)
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