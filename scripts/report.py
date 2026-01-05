#!/usr/bin/env python3
import time
import struct
from panda import Panda

def reporter():
    try:
        p = Panda()
        
        print("Starting EV Reporter")
        
        while True:
            # Read Hardware Voltage (The internal sensor)
            # The panda health dictionary contains 'voltage' in millivolts
            voltage = 0
            health = p.health()
            if 'voltage' in health:
                voltage = health['voltage'] / 1000.0

            if voltage > 0:
                // Do network request
                break
            
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    reporter()
