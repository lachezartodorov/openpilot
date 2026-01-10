import time
import struct
from datetime import datetime
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 1         # 0=CAN1, 1=CAN2 (Check your wiring!)
BUS_SPEED  = 100       # VW Comfort CAN is 100kbps (Low Speed CAN)
WAKE_ID    = 0x69D     # OCU Heartbeat ID
READ_ID    = 0x61C     # Climate Target Temp ID

def get_wake_message():
    """
    Constructs the VAG OCU heartbeat message: 0x69D
    Payload: [0x24, Year, Month, Day, Hour, Min, Sec, 0x00]
    """
    now = datetime.now()
    year_byte = now.year % 100
    # Structure based on vweup_t26.cpp implementation
    return struct.pack("BBBBBBBB", 0x24, year_byte, now.month, now.day, now.hour, now.minute, now.second, 0x00)

def main():
    try:
        print("[*] Initializing Panda...")
        p = Panda()

        # 1. Set Bus Speed (Critical for Comfort CAN)
        print(f"[*] Setting Bus {TARGET_BUS} to {BUS_SPEED} kbps...")
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)

        # 2. Safety Mode (Allow TX)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        last_wake_tx = 0
        last_wake_rx = 0
        wake_interval = 1.0  # Send every 1 second

        print(f"[*] Listening for Target Temp {hex(READ_ID)} and managing Wake {hex(WAKE_ID)}...")

        while True:
            current_time = time.time()
            incoming = p.can_recv()

            # --- PROCESS INCOMING FRAMES ---
            for address, _, dat, src_bus in incoming:
                if src_bus != TARGET_BUS:
                    continue

                # 1. Check if real OCU is alive
                if address == WAKE_ID:
                    last_wake_rx = current_time # Update that we saw a real heartbeat

                # 2. Read Target Temp
                if address == READ_ID:
                    if len(dat) > 0:
                        raw_val = dat[0]
                        temp_c = raw_val / 2.0  # Formula: Raw / 2
                        print(f" -> Climate Setpoint: {temp_c:.1f}°C (Raw: {raw_val})")

            # --- SMART WAKE LOGIC ---
            # Only send our fake heartbeat if we haven't seen a real one for 2 seconds
            ocu_is_silent = (current_time - last_wake_rx) > 2.0
            time_to_send  = (current_time - last_wake_tx) > wake_interval

            if ocu_is_silent and time_to_send:
                msg = get_wake_message()
                p.can_send(WAKE_ID, msg, TARGET_BUS)
                last_wake_tx = current_time
                # print(f" <- Sending Keep-Alive (Simulating OCU)")

            elif not ocu_is_silent and time_to_send:
                # Optional debug to know why we aren't sending
                # print(" [!] Real OCU detected. Staying silent to avoid conflict.")
                pass

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        p.set_safety_mode(Panda.SAFETY_SILENT)
    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    main()