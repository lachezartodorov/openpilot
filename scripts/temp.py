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
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        # Store "noisy" bits: {ID: [True, False, ...]}
        noise_mask = {}
        last_data = {}

        print("\n--- PHASE 1: CALIBRATION (Do NOT touch anything!) ---")
        print("    Measuring background noise for 5 seconds...")

        start_time = time.time()
        while time.time() - start_time < 5.0:
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if bus != TARGET_BUS: continue

                current_bytes = list(dat)

                if addr not in last_data:
                    last_data[addr] = current_bytes
                    noise_mask[addr] = [False] * len(dat)
                else:
                    # FIX: Handle variable packet lengths
                    # 1. Extend noise_mask if current packet is longer
                    if len(current_bytes) > len(noise_mask[addr]):
                        extend_len = len(current_bytes) - len(noise_mask[addr])
                        noise_mask[addr].extend([False] * extend_len)

                    # 2. Compare bytes (safely iterating up to the length of the shorter packet)
                    prev_bytes = last_data[addr]
                    min_len = min(len(current_bytes), len(prev_bytes))

                    for i in range(min_len):
                        if current_bytes[i] != prev_bytes[i]:
                            noise_mask[addr][i] = True

                    last_data[addr] = current_bytes

            # Print a dot to show aliveness
            if int(time.time() * 10) % 10 == 0:
                print(".", end="", flush=True)
                time.sleep(0.1)

        print("\n\n--- PHASE 2: DETECTION (NOW turn the Knob!) ---")
        print("    Ignoring background noise. Turn the temp knob to see signal...")

        # Reset last_data to ensure we catch the immediate change
        last_data = {}

        while True:
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if bus != TARGET_BUS: continue

                current_bytes = list(dat)

                # Skip IDs we haven't seen (or just initialize them blindly to avoid crash)
                if addr not in noise_mask:
                    noise_mask[addr] = [False] * len(dat)
                    last_data[addr] = current_bytes
                    continue

                # Ensure mask is long enough (safety catch for Phase 2)
                if len(current_bytes) > len(noise_mask[addr]):
                     noise_mask[addr].extend([False] * (len(current_bytes) - len(noise_mask[addr])))

                if addr not in last_data:
                    last_data[addr] = current_bytes
                    continue

                prev_bytes = last_data[addr]
                is_real_change = False

                # Compare
                min_len = min(len(current_bytes), len(prev_bytes))

                for i in range(min_len):
                    # Check if byte changed AND is NOT noise
                    if current_bytes[i] != prev_bytes[i] and not noise_mask[addr][i]:
                        is_real_change = True
                        break # Optimization: one real change is enough to trigger print

                if is_real_change:
                    # Build pretty string
                    hex_str = []
                    for i in range(len(current_bytes)):
                        val = current_bytes[i]
                        b_str = f"{val:02x}"

                        # Logic to decorate the changed byte
                        # We need to be careful with index bounds here too
                        is_noise = noise_mask[addr][i] if i < len(noise_mask[addr]) else False
                        was_diff = False
                        if i < len(prev_bytes):
                            if val != prev_bytes[i]:
                                was_diff = True

                        if was_diff and not is_noise:
                            hex_str.append(f"[{b_str}]") # SIGNAL
                        elif is_noise:
                            hex_str.append(f"{b_str}")   # NOISE (plain)
                        else:
                            hex_str.append(b_str)        # STATIC

                    print(f" [!] CHANGE ID {hex(addr)}: {' '.join(hex_str)}")

                    last_data[addr] = current_bytes

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n[*] Stopping...")
        p.set_safety_mode(Panda.SAFETY_SILENT)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()