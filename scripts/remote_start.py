#!/usr/bin/env python3
import time
import logging
from panda import Panda

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

def main():
    try:
        logging.info("Connecting to Panda...")
        p = Panda()

        # --- CRITICAL FIX 1: SET BAUD RATE ---
        # Comfort CAN is 100kbps. We MUST force the Panda to this speed.
        logging.info("Setting Bus 1 to 100kbps...")
        p.set_can_speed_kbps(1, 100)

        # Set safety to allow output
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        BUS = 1

        # --- CRITICAL FIX 2: NETWORK MANAGEMENT (NM) WAKEUP ---
        # ID 0x320 is the Gateway's own 'Wakeup/Status' ID.
        # Sending this mimics the Gateway telling modules to stay awake.
        nm_wakeup_id = 0x320
        nm_data = b"\x01\x00\x00\x01\x00\x00\x00\x80"

        logging.info("Step 1: Flooding Network Management to wake the Gateway...")
        for _ in range(50): # Send for 0.5 seconds
            p.can_send(nm_wakeup_id, nm_data, BUS)
            time.sleep(0.01)

        # --- STEP 2: THE AC COMMAND ---
        # We will send the AC command for a longer duration (5 seconds)
        ac_start_id = 0x69E
        ac_start_data = b"\x01\x00\x00\x00\x00\x00\x00\x00"

        logging.info("Step 2: Sending AC Start command (0x69E)...")
        start_time = time.time()
        while time.time() - start_time < 5.0:
            # We keep sending the NM message in the background to keep the bus awake
            p.can_send(nm_wakeup_id, nm_data, BUS)

            # Send the actual AC command
            p.can_send(ac_start_id, ac_start_data, BUS)
            time.sleep(0.05)

        logging.info("Done. Check if the AC LED is on or if you hear the compressor.")
        p.close()

    except Exception as e:
        logging.error(f"Error: {e}")

if __name__ == "__main__":
    main()