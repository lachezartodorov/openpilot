#!/usr/bin/env python3
import time
import struct
from panda import Panda

# --- LIVE DASHBOARD STORAGE ---
dashboard = {
    "SOC": 0,
    "Range": 0,
    "12V_Hardware": 0,    # Reading from Panda hardware
    "Charging_Active": "No",
    "Plug_Connected": "No",
    "Doors": "Closed",
}

# --- DECODERS ---

def decode_61A_soc(data):
    # ID: 0x61A (Ladegeraet_1) - SOC
    if len(data) >= 8:
        dashboard["SOC"] = data[7] / 2.0

def decode_52D_range(data):
    # ID: 0x52D (Range)
    if len(data) >= 2:
        dashboard["Range"] = data[0]

def decode_61C_charge_status(data):
    # ID: 0x61C (Charger Status)
    if len(data) >= 3:
        # Byte 1: Plug State
        # 0xF0 = Unplugged / Idle
        # 0x03/0x04 = Connected
        plug_byte = data[1]
        
        # New Logic: Only say "Yes" if it looks like a valid cable (small numbers)
        if plug_byte in [0x03, 0x04, 0x01]: 
             dashboard["Plug_Connected"] = "YES"
        else:
             dashboard["Plug_Connected"] = "No"
             
        # Byte 2: Charging State
        # 0x0F = Standby/Done
        # < 7 = Active
        charge_byte = data[2]
        dashboard["Charging_Active"] = "Yes" if charge_byte < 7 else "No"

def decode_470_doors(data):
    # ID: 0x470 (Doors) - You said this works!
    if len(data) >= 2:
        val = data[1]
        # Bitmask check
        if (val & 0x1F) > 0: 
            dashboard["Doors"] = "OPEN"
        else:
            dashboard["Doors"] = "Closed"

# Map IDs to Functions
decoders = {
    0x61A: decode_61A_soc,
    0x52D: decode_52D_range,
    0x61C: decode_61C_charge_status,
    0x470: decode_470_doors,
}

def run_dashboard_v3():
    try:
        p = Panda()
        p.set_safety_mode(Panda.SAFETY_SILENT)
        
        print("Starting EV Dashboard 3.0 (Fixed Logic)...")
        
        while True:
            # 1. Read CAN
            incoming = p.can_recv()
            for msg in incoming:
                addr = msg[0]
                data = msg[1]
                if addr in decoders:
                    try:
                        decoders[addr](data)
                    except:
                        pass
            
            # 2. Read Hardware Voltage (The internal sensor)
            # The panda health dictionary contains 'voltage' in millivolts
            health = p.health()
            if 'voltage' in health:
                dashboard["12V_Hardware"] = health['voltage'] / 1000.0

            # 3. Print Output
            output = f"""
            ========================================
            VW e-Up LIVE MONITOR (Bus 0)
            ========================================
            Battery SOC    : {dashboard['SOC']:.1f} %
            Range (Est)    : {dashboard['Range']} km
            12V Battery    : {dashboard['12V_Hardware']:.2f} V (Hw)
            ----------------------------------------
            Plugged In     : {dashboard['Plug_Connected']}
            Charging       : {dashboard['Charging_Active']}
            Doors          : {dashboard['Doors']}
            ========================================
            """
            print('\033[H\033[J' + output)
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    run_dashboard_v3()
