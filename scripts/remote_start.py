#!/usr/bin/env python3
import time
import logging
from panda import Panda

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

def main():
    try:
        logging.info("Connecting to Panda...")
        p = Panda()

        # Ensure Bus 1 (Comfort) is at the correct 100kbps speed
        p.set_can_speed_kbps(1, 100)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        BUS = 1

        # 0x320: Network Management (NM) Wakeup - mimics the Gateway being active
        nm_id = 0x320
        nm_data = b"\x01\x00\x00\x01\x00\x00\x00\x80"

        # 0x69E: Remote AC Start Command
        ac_id = 0x69E
        ac_data = b"\x01\x00\x00\x00\x00\x00\x00\x00"

        logging.info("Step 1: Initial Wakeup Burst...")
        for _ in range(100):
            p.can_send(nm_id, nm_data, BUS)
            time.sleep(0.01)

        # Step 2: The Long Hold (Crucial for the 17-second delay)
        logging.info("Step 2: Sending AC Start command. This will take 30 seconds...")
        logging.info("Listen for the High Voltage contactors (loud click) around 15-20s.")

        start_time = time.time()
        while time.time() - start_time < 30.0:
            # Send both messages to keep the bus 'alive' while requesting AC
            p.can_send(nm_id, nm_data, BUS)
            p.can_send(ac_id, ac_data, BUS)

            # Send at 10Hz (every 100ms) to avoid flooding but stay persistent
            time.sleep(0.1)

        logging.info("Sequence complete. Wait another 10s to see if the fans start.")
        p.close()

    except Exception as e:
        logging.error(f"Error: {e}")

if __name__ == "__main__":
    main()