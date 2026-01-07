#!/usr/bin/env python3
import time
import sys
import struct
from panda import Panda

# --- CONFIGURATION ---
LOG_FILE = '/data/log/ignition_off_log.txt'

# 1. Force Unbuffered Output (So data hits the file instantly)
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
        return

    # Open Log File
    with open(LOG_FILE, 'a') as f:
        header = f"--- STARTING LOG (Serial: {serial}) ---\n"
        print(header, end='')
        f.write(header)

        # Dictionary to store the last known data for each (Bus, ID)
        # Key: (bus, address) -> Value: byte_data
        seen_messages = {}

        start_time = time.time()

        print("Listening for UNIQUE messages... (Press CTRL+C to stop)")
        print(f"Logging to: {LOG_FILE}")

        try:
            while True:
                incoming = p.can_recv()

                for msg in incoming:
                    address = msg[0]
                    data = msg[1] # Raw bytes
                    bus = msg[3]  # Bus ID

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

                        # Construct Log Line
                        # Format: [Time] Bus X | ID: 0x123 | Len: 8 | Data: ... | ASCII
                        log_line = (
                            f"[{rel_time:8.3f}] "
                            f"Bus {bus} | "
                            f"ID: {hex(address):<6} ({address:<4}) | "
                            f"Len: {len(data)} | "
                            f"Data: {hex_data:<16} | "
                            f"Txt: {ascii_data}"
                        )

                        # Print to Console (for SSH watching)
                        print(log_line)

                        # Write to File
                        f.write(log_line + "\n")

                # Sleep briefly to prevent CPU hogging
                time.sleep(0.005)

        except KeyboardInterrupt:
            footer = "\n--- STOPPED BY USER ---\n"
            print(footer)
            f.write(footer)

if __name__ == "__main__":
    run_scanner()