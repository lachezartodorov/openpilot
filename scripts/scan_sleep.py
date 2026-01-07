#!/usr/bin/env python3
import time
import sys
import os
from panda import Panda

# --- CONFIGURATION ---
# Ensure directory exists
LOG_DIR = '/data/log'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE = f'{LOG_DIR}/ignition_off_log.txt'

# 1. Force Unbuffered Output
sys.stdout.reconfigure(line_buffering=True)

def safe_ascii(data):
    """Tries to convert bytes to ASCII chars, prints '.' if not printable"""
    txt = ""
    for b in data:
        if 32 <= b <= 126: # Printable ASCII range
            txt += chr(b)
        else:
            txt += "."
    return txt

def run_scanner():
    # Connect to Panda
    try:
        p = Panda()
        p.set_safety_mode(Panda.SAFETY_SILENT)
        serial = p.get_serial()
    except Exception as e:
        print(f"CRITICAL: Could not connect to Panda. {e}")
        print("HINT: Did you run 'pkill -f pandad' first?")
        return

    # Open Log File
    with open(LOG_FILE, 'a') as f:
        header = f"--- STARTING LOG (Serial: {serial}) ---\n"
        print(header, end='')
        f.write(header)

        # Dictionary to store the last known data for each (Bus, ID)
        seen_messages = {}

        start_time = time.time()

        print("Listening for UNIQUE messages... (Press CTRL+C to stop)")
        print(f"Logging to: {LOG_FILE}")

        try:
            while True:
                incoming = p.can_recv()

                for msg in incoming:
                    # --- FIXED INDEXING FOR YOUR DEVICE ---
                    # Structure is (Address, Data, Bus)
                    address = msg[0]
                    data = msg[1]
                    bus = msg[2]  # <--- Changed from msg[3] to msg[2]

                    # Create a unique key for this specific message source
                    msg_key = (bus, address)

                    # CHECK: Is this new or different?
                    if (msg_key not in seen_messages) or (seen_messages[msg_key] != data):

                        # Update our memory
                        seen_messages[msg_key] = data

                        # Calculate Timestamp
                        rel_time = time.time() - start_time

                        # Formatting
                        hex_data = data.hex()
                        ascii_data = safe_ascii(data)

                        # Format: [Time] Bus X | ID: 0x123 | Len: 8 | Data: ... | ASCII
                        log_line = (
                            f"[{rel_time:8.3f}] "
                            f"Bus {bus} | "
                            f"ID: {hex(address):<6} ({address:<4}) | "
                            f"Len: {len(data)} | "
                            f"Data: {hex_data:<16} | "
                            f"Txt: {ascii_data}"
                        )

                        print(log_line)
                        f.write(log_line + "\n")

                # Sleep briefly to prevent CPU hogging
                time.sleep(0.005)

        except KeyboardInterrupt:
            footer = "\n--- STOPPED BY USER ---\n"
            print(footer)
            f.write(footer)

if __name__ == "__main__":
    run_scanner()