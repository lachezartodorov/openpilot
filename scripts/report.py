#!/usr/bin/env python3
import time
import json
from panda import Panda
import urllib.request
import urllib.error

def reporter():
    try:
        p = Panda()
        print("Starting EV Reporter")

        while True:
            # Read Hardware Voltage (The internal sensor)
            # The panda health dictionary contains 'voltage' in millivolts
            voltage = 0.0
            health = p.health()
            if 'voltage' in health:
                try:
                    voltage = float(health['voltage']) / 1000.0
                except Exception:
                    voltage = 0.0

            if voltage > 0:
                # Do network request: POST telemetry to ThingsBoard demo instance
                url = "https://demo.thingsboard.io/api/v1/PBMXSn7TRsCq57tkUAla/telemetry"
                payload = json.dumps({"battery": voltage}).encode("utf-8")
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

                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    reporter()
