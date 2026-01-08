#!/usr/bin/env python3

import time
import json
from panda import Panda
import urllib.request
import urllib.error
import logging
import sys

# Setup logging to a file in /data/
# Added force=True and unbuffered stderr to ensure logs are written during offroad
sys.stderr.reconfigure(line_buffering=True)
logging.basicConfig(
    filename='/data/log/report_script_internal.log',
    level=logging.INFO,
    filemode='a',
    format='%(asctime)s %(levelname)s: %(message)s',
    force=True
)

dashboard = {
    "soc": -1,
    "soc2": -1,
    "range": -1,
    "charging": "-",
    "plugged_in": "-",
    "doors": "-",
    "battery": -1,
    "odometer": -1,
    "temp": -1,
}

# --- DECODERS ---

def decode_470_lock_status(data):
    # ID: 0x470 (Gate_Komf_1)
    # Byte 1 typically contains door/lock states in VW PQ
    if len(data) >= 2:
        # Check bits for "Locked" vs "Unlocked" based on your log transitions
        # 0x00 at Byte 1 often means Locked, 0x03 or higher means Unlocked/Open
        status_byte = data[1]
        if status_byte == 0x00:
            dashboard["doors"] = "Locked"
        else:
            dashboard["doors"] = "Unlocked/Open"

def decode_61A_soc(data):
    # ID: 0x61A (Ladegeraet_1)
    # Based on VW PQ DBC: Byte 0 * 0.5 = SOC %
    if len(data) >= 8:
        dashboard["soc"] = data[7] / 2.0
    if len(data) >= 1:
        dashboard["soc2"] = data[0] * 0.5

def decode_52D_range(data):
    # ID: 0x52D (Range)
    if len(data) >= 1:
        dashboard["range"] = data[0]

def decode_527_temp(data):
    # ID: 0x527 (Klima_1)
    # Typically Byte 5 is Ambient Temp: (Value * 0.5) - 40
    if len(data) >= 6:
        dashboard["temp"] = (data[5] * 0.5) - 40

def decode_658_odometer(data):
    # ID: 0x658 (Odometer)
    # Bytes 1, 2, and 3 form a 24-bit integer
    if len(data) >= 4:
        dashboard["odometer"] = (data[3] << 16) | (data[2] << 8) | data[1]

def decode_61C_charge_status(data):
    # ID: 0x61C (Charger Status)
    if len(data) >= 3:
        plug_byte = data[1]
        # 0xF0 = Unplugged, 0x03/0x04 = Connected
        dashboard["plugged_in"] = "Yes" if plug_byte < 0xF0 else "No"
        # Simple charging check
        dashboard["charging"] = "Yes" if dashboard["plugged_in"] == "Yes" and data[2] > 0 else "No"

def main():
    logging.info("Reporting script started with full decoder set.")
    try:
        # Bus 1 is where we wired the Comfort CAN
        BUS_COMFORT = 1

        while True:
            try:
                p = Panda()
                p.set_safety_mode(Panda.SAFETY_SILENT)
            except Exception as e:
                logging.error(f"Panda connection failed: {e}. Retrying in 30s...")
                time.sleep(30)
                continue

            i = 0
            # Sample for ~20 seconds to catch all messages
            while i < 400:
                can_recv = p.can_recv()
                for addr, dat, src in can_recv:
                    if src == BUS_COMFORT:
                        if addr == 0x470:
                            decode_470_lock_status(dat)
                        elif addr == 0x61A:
                            decode_61A_soc(dat)
                        elif addr == 0x52D:
                            decode_52D_range(dat)
                        elif addr == 0x527:
                            decode_527_temp(dat)
                        elif addr == 0x658:
                            decode_658_odometer(dat)
                        elif addr == 0x61C:
                            decode_61C_charge_status(dat)

                i += 1
                time.sleep(0.05)

            # Get 12V Battery Voltage from Panda health
            try:
                h = p.health()
                voltage = h['voltage'] / 1000.0
                dashboard["battery"] = round(voltage, 2)
            except:
                voltage = -1

            # Prepare Telemetry
            url = "https://demo.thingsboard.io/api/v1/PBMXSn7TRsCq57tkUAla/telemetry"
            payload = json.dumps(dashboard).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logging.info(dashboard)
            except Exception as e:
                logging.error(f"Post failed: {e}")

            # Close panda to allow other processes if necessary, then sleep
            p.close()

            # Wait 2 minutes before next update to save 12V battery
            logging.info("Sleeping for 2 minutes...")
            time.sleep(120)

    except KeyboardInterrupt:
        logging.info("Stopped by user.")
    except Exception as e:
        logging.error(f"Fatal Error: {e}")

if __name__ == "__main__":
    main()
