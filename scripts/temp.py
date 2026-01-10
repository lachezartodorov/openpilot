import time
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

def main():
    try:
        print("[*] Initializing Panda...")
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT) # Needed to keep car awake

        # Store "noisy" bits: {ID: [True, False, False, ...]}
        # True means "this byte is noise", False means "this byte is signal"
        noise_mask = {}
        last_data = {}

        print("\n--- PHASE 1: CALIBRATION (Do NOT touch anything!) ---")
        print("    Measuring background noise for 5 seconds...")

        start_time = time.time()
        while time.time() - start_time < 5.0:
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if bus != TARGET_BUS: continue

                # Convert bytearray to list of integers for easier comparison
                current_bytes = list(dat)

                if addr not in last_data:
                    last_data[addr] = current_bytes
                    # Initialize mask: assume all bytes are signal (False) initially
                    noise_mask[addr] = [False] * len(dat)
                else:
                    # Compare with previous packet
                    prev_bytes = last_data[addr]
                    if len(prev_bytes) == len(current_bytes):
                        for i in range(len(current_bytes)):
                            if current_bytes[i] != prev_bytes[i]:
                                # If it changed while user did nothing, it's NOISE.
                                noise_mask[addr][i] = True

                    last_data[addr] = current_bytes

            # Print a dot every second to show progress
            if int(time.time()) > int(start_time):
                print(".", end="", flush=True)
                start_time += 0.1 # slight cheat to not flood print

        print("\n\n--- PHASE 2: DETECTION (NOW turn the Knob!) ---")
        print("    Ignoring counters/checksums. Waiting for real changes...")

        # Clear recent history so we catch the first turn
        last_data = {}

        while True:
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if bus != TARGET_BUS: continue

                current_bytes = list(dat)

                # Skip IDs we didn't see during calibration (rare events)
                if addr not in noise_mask:
                    continue

                if addr not in last_data:
                    last_data[addr] = current_bytes
                    continue

                # Check for "Real" changes
                prev_bytes = last_data[addr]
                is_real_change = False

                if len(prev_bytes) == len(current_bytes):
                    for i in range(len(current_bytes)):
                        # If byte changed AND it is NOT marked as noise
                        if current_bytes[i] != prev_bytes[i] and not noise_mask[addr][i]:
                            is_real_change = True
                            # Highlight this byte in the print output
                            # (We handle the print below)

                if is_real_change:
                    # Construct a visual string
                    hex_str = []
                    for i in range(len(current_bytes)):
                        b_str = f"{current_bytes[i]:02x}"
                        if current_bytes[i] != prev_bytes[i] and not noise_mask[addr][i]:
                            # Highlight changed signal byte with []
                            hex_str.append(f"[{b_str}]")
                        elif noise_mask[addr][i]:
                            # Dim out noise bytes (optional, or just print normal)
                            hex_str.append(f"{b_str}")
                        else:
                            hex_str.append(b_str)

                    print(f" [!] CHANGE ID {hex(addr)}: {' '.join(hex_str)}")

                    # Update comparison reference
                    last_data[addr] = current_bytes

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        p.set_safety_mode(Panda.SAFETY_SILENT)

if __name__ == "__main__":
    main()