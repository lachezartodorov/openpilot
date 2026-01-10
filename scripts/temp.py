import time
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps
IGNORE_IDS = [0x69D]   # Ignore our own heartbeat

def main():
    try:
        print("[*] Initializing Panda for Change Detection...")
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT) # Needed to wake the car if you use the heartbeat

        # Dictionary to store the last known data for each ID
        # Format: {ID: b'\x00\x01...'}
        last_data = {}

        print("\n[*] Sniffer Running. Turn the TEMP KNOB now!")
        print("[*] Watching for changes... (Press Ctrl+C to stop)\n")

        while True:
            incoming = p.can_recv()

            for address, dat, src_bus in incoming:
                if src_bus != TARGET_BUS:
                    continue

                if address in IGNORE_IDS:
                    continue

                # Check if this is a new ID or if data has changed
                if address not in last_data:
                    last_data[address] = dat
                elif last_data[address] != dat:
                    # DATA CHANGED! Print it.
                    old_hex = last_data[address].hex()
                    new_hex = dat.hex()

                    # Highlight the changed bytes could be fancy, but simple print is enough
                    print(f" [!] CHANGE DETECTED on ID {hex(address)}:")
                    print(f"     Old: {old_hex}")
                    print(f"     New: {new_hex}")
                    print("-" * 30)

                    # Update memory
                    last_data[address] = dat

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        p.set_safety_mode(Panda.SAFETY_SILENT)
    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    main()