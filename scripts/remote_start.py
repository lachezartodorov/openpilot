#!/usr/bin/env python3

import time
import logging
from panda import Panda

# Setup logging
logging.basicConfig(
    filename='/data/log/remote_start.log',
    level=logging.INFO,
    filemode='a',
    format='%(asctime)s %(levelname)s: %(message)s',
    force=True
)

def main():
    try:
        # Initialize Panda
        logging.info("Connecting to Panda...")
        p = Panda()

        # We use Bus 1 (Comfort CAN) for these commands
        BUS = 1

        # 1. THE WAKE-UP COMMAND
        # Mimics pushing the physical 'Lock' button on the door to wake the BCM/Gateway
        # ID: 0x291, Byte 6: 0x09
        wake_msg = [0x291, 0, b"\x00\x00\x00\x00\x00\x00\x09\x00", BUS]

        logging.info("Step 1: Sending Wake-up command (Mimic Lock)...")
        for _ in range(10):
            p.can_send(*wake_msg)
            time.sleep(0.01)

        # Wait 1.5 seconds for the Gateway to fully stabilize and modules to check-in
        # You saw in your logs that it takes about this long for the bus to 'burst'
        time.sleep(1.5)

        # 2. THE AC START COMMAND
        # ID: 0x69E (Standard VW PQ Remote AC Start)
        # Byte 0: 0x01 (Activate)
        ac_start_msg = [0x69E, 0, b"\x01\x00\x00\x00\x00\x00\x00\x00", BUS]

        logging.info("Step 2: Sending AC Start command...")
        # We send this for 3 seconds because the car often needs to see a
        # persistent signal to verify it isn't a random glitch.
        start_time = time.time()
        while time.time() - start_time < 3.0:
            p.can_send(*ac_start_msg)
            time.sleep(0.05) # 20Hz frequency

        logging.info("Commands sent successfully.")

        # Clean up
        p.close()

    except Exception as e:
        logging.error(f"Failed to execute remote start: {e}")
        logging.error("Make sure openpilot (pandad) is not running!")

if __name__ == "__main__":
    main()