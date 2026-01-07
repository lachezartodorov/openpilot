#!/usr/bin/env python3

import time
import logging
from panda import Panda

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

def main():
    try:
        # Initialize Panda
        logging.info("Connecting to Panda...")
        p = Panda()

        # IMPORTANT: Set safety mode to allow sending
        # 0x1337 is 'SAFETY_ALLOUTPUT', which allows manual CAN injection
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        # We use Bus 1 (Comfort CAN) for these commands
        BUS = 1

        # 1. THE WAKE-UP COMMAND
        # Mimics pushing the physical 'Lock' button on the door to wake the BCM/Gateway
        # Structure: [ID, Data (bytes), Bus]
        wake_msg_id = 0x291
        wake_msg_data = b"\x00\x00\x00\x00\x00\x00\x09\x00"

        logging.info("Step 1: Sending Wake-up command (Mimic Lock)...")
        for _ in range(10):
            p.can_send(wake_msg_id, wake_msg_data, BUS)
            time.sleep(0.01)

        # Wait 1.5 seconds for the Gateway to fully stabilize
        time.sleep(1.5)

        # 2. THE AC START COMMAND
        # ID: 0x69E (Standard VW PQ Remote AC Start)
        ac_start_id = 0x69E
        ac_start_data = b"\x01\x00\x00\x00\x00\x00\x00\x00"

        logging.info("Step 2: Sending AC Start command...")
        # Send for 3 seconds to ensure the car accepts the request
        start_time = time.time()
        while time.time() - start_time < 3.0:
            p.can_send(ac_start_id, ac_start_data, BUS)
            time.sleep(0.05) # 20Hz frequency

        logging.info("Commands sent successfully.")

        p.close()

    except Exception as e:
        logging.error(f"Failed to execute remote start: {e}")
        logging.error("Make sure openpilot (pandad) is not running!")

if __name__ == "__main__":
    main()