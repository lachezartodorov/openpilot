#!/usr/bin/env python3

import time
import json
from panda import Panda
import urllib.request
import urllib.error

dashboard = {
    "soc": -1,
    "range": -1,
    "charging": "-",
    "plugged_in": "-",
    "doors": "-",
    "battery": -1,
}

# --- DECODERS ---

def decode_61A_soc(data):
    # ID: 0x61A (Ladegeraet_1) - SOC
    if len(data) >= 8:
        dashboard["soc"] = data[7] / 2.0

def decode_52D_range(data):
    # ID: 0x52D (Range)
    if len(data) >= 2:
        dashboard["range"] = data[0]

def decode_61C_charge_status(data):
    # ID: 0x61C (Charger Status)
    if len(data) >= 3:
        # Byte 1: Plug State
        # 0xF0 = Unplugged / Idle
        # 0x03/0x04 = Connected
        plug_byte = data[1]

        # New Logic: Only say "Yes" if it looks like a valid cable (small numbers)
        if plug_byte in [0x03, 0x04, 0x01]:
             dashboard["plugged_in"] = "YES"
        else:
             dashboard["plugged_in"] = "No"

        # Byte 2: Charging State
        # 0x0F = Standby/Done
        # < 7 = Active
        charge_byte = data[2]
        dashboard["charging"] = "Yes" if charge_byte < 7 else "No"

def decode_470_doors(data):
    # ID: 0x470 (Doors) - You said this works!
    if len(data) >= 2:
        val = data[1]
        # Bitmask check
        if (val & 0x1F) > 0:
            dashboard["doors"] = "OPEN"
        else:
            dashboard["doors"] = "Closed"

# Map IDs to Functions
decoders = {
    0x61A: decode_61A_soc,
    0x52D: decode_52D_range,
    0x61C: decode_61C_charge_status,
    0x470: decode_470_doors,
}


def main():
    try:
        i = 0
        sleepTime = 0.3
        p = Panda()
        print("Starting EV Reporter")

        while True:
            i += 1

            # Read Hardware Voltage (The internal sensor)
            # The panda health dictionary contains 'voltage' in millivolts
            health = p.health()
            if 'voltage' in health:
                try:
                    dashboard["battery"] = float(health['voltage']) / 1000.0
                except Exception:
                    dashboard["battery"] = 0.0

            # Read CAN
            incoming = p.can_recv()
            for msg in incoming:
                addr = msg[0]
                data = msg[1]
                if addr in decoders:
                    try:
                        decoders[addr](data)
                    except:
                        pass

            if i > 30 or (dashboard["battery"] > 0 and dashboard["range"] > 0):
                print(f"Sending data: {dashboard}")
                sleepTime = 120
                
                # Do network request: POST telemetry to ThingsBoard demo instance
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
                        status = resp.getcode()
                        body = resp.read().decode("utf-8", errors="replace")
                        print(f"Posted battery={voltage} V -> status={status}, body={body}")
                except urllib.error.HTTPError as he:
                    err_body = he.read().decode("utf-8", errors="replace") if hasattr(he, "read") else ""
                    print(f"HTTPError posting telemetry: {he.code} {he.reason}. Body: {err_body}")
                except urllib.error.URLError as ue:
                    print(f"URLError posting telemetry: {ue.reason}")
                except Exception as e:
                    print(f"Unexpected error posting telemetry: {e}")


            time.sleep(sleepTime)

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    main()
