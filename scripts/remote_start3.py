import time
import struct
import threading
import sys
from datetime import datetime
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps
WAKE_ID    = 0x69D     # Heartbeat ID
CMD_ID     = 0x69E     # Remote Control ID
READ_ID    = 0x61C     # Temp Feedback
AC_TARGET_TEMP = 21.0  # Default to 21.0°C

# Global flags
keep_running = True
ac_request   = None    # None, "START", or "STOP"

def get_wake_message():
    """Generates the OCU Heartbeat: [0x24, YY, MM, DD, HH, MM, SS, 00]"""
    now = datetime.now()
    return struct.pack("BBBBBBBB", 0x24, now.year % 100, now.month, now.day, now.hour, now.minute, now.second, 0x00)

def get_climate_command(enable=True, temp_c=21.0):
    """
    Generates the Climate Control Command (0x69E).
    Byte 0: 0x80 = ON, 0x40 = OFF
    Byte 1: 0x04 = Climate Mode
    Byte 2: Temp * 2 (Raw)
    """
    cmd_byte = 0x80 if enable else 0x40
    temp_raw = int(temp_c * 2)
    # Payload: [Cmd, Mode, Temp, 00, 00, 00, 00, 00]
    return struct.pack("BBBBBBBB", cmd_byte, 0x04, temp_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

def connection_thread(p):
    """Background thread to handle Heartbeats and AC Commands"""
    global ac_request

    last_wake = 0
    wake_interval = 1.0

    print("[*] Background thread started. Heartbeat active.")

    while keep_running:
        current_time = time.time()

        # 1. SEND HEARTBEAT
        if current_time - last_wake > wake_interval:
            wake_msg = get_wake_message()
            try:
                p.can_send(WAKE_ID, wake_msg, TARGET_BUS)
                last_wake = current_time
            except Exception as e:
                print(f"[!] TX Error: {e}")

        # 2. READ & DEBUG 0x61C (Target Temp)
        # We peek at the bus here inside the thread to get data
        incoming = p.can_recv()
        for addr, dat, bus in incoming:
            if addr == READ_ID and bus == TARGET_BUS:
                # PRINT THE FULL RAW DATA
                # Look for a value around 0x2A (42) -> 21°C or 0x2C (44) -> 22°C
                print(f" [Rx] ID 0x61C Raw: {dat.hex()}")

        # 3. HANDLE AC REQUESTS
        if ac_request == "START":
            print(f" -> TX: Sending AC ON ({AC_TARGET_TEMP}°C)...")
            cmd = get_climate_command(enable=True, temp_c=AC_TARGET_TEMP)
            for _ in range(5):
                p.can_send(CMD_ID, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

        elif ac_request == "STOP":
            print(" -> TX: Sending AC OFF...")
            cmd = get_climate_command(enable=False, temp_c=AC_TARGET_TEMP)
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
        print(f"[!] Error connecting to Panda: {e}")
        return

    # Start the background heartbeat/command thread
    t = threading.Thread(target=connection_thread, args=(p,))
    t.start()

    print("\n--- CONTROLS ---")
    print(" 1 : Turn AC ON (21°C)")
    print(" 0 : Turn AC OFF")
    print(" q : Quit")
    print("----------------")

    # Main thread listens for user input and CAN feedback
    try:
        while True:
            # Non-blocking input check is hard in standard Python,
            # so we'll just use a blocking input for control.
            # To see updates, we rely on the print statements from the thread.

            user_input = input("Cmd > ").strip().lower()

            if user_input == '1':
                ac_request = "START"
            elif user_input == '0':
                ac_request = "STOP"
            elif user_input == 'q':
                break

            # Simple Feedback Loop (Read 0x61C momentarily)
            # In a real GUI app, this would be constantly reading.
            # Here we just peek at the buffer.
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if addr == READ_ID and bus == TARGET_BUS and len(dat) > 0:
                    val = dat[0] / 2.0
                    print(f" [Rx] Current Setpoint: {val}°C")

    except KeyboardInterrupt:
        pass
    finally:
        keep_running = False
        t.join()
        p.set_safety_mode(Panda.SAFETY_SILENT)
        print("\n[*] Exited.")

if __name__ == "__main__":
    main()