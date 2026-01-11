#!/usr/bin/env python3
"""
VW e-UP! Remote AC and Charging Control via Panda
Based on OVMS vweup_t26.cpp logic, adapted for Comma/Panda communication

Usage:
    python3 omega.py
    # Or set write bus ID:
    WRITE_BUS=0 python3 omega.py  # 0=CAN1, 1=CAN2, 2=CAN3

Controls:
    w - Wakeup T26 (Comfort CAN)
    r - Read Profile0 (charging/climate settings)
    a - AC ON
    s - AC OFF
    t - Toggle AC temperature (21°C/24°C)
    c <amps> - Set charge current limit (e.g., 'c 16' for 16A)
    q - Quit

The dashboard shows:
    - Vehicle information (VIN, speed, odometer, drive lever)
    - Battery & charging status (SOC, range, charge state, limits)
    - Climate control status (HVAC, temperatures)
    - Vehicle status (doors, locked, lights, etc.)
    - OCU/Ring state
    - Recent CAN messages with bus IDs

All CAN messages are decoded and displayed with their bus ID.
The write bus ID is configurable via WRITE_BUS environment variable.
"""

import time
import struct
import threading
import os
from datetime import datetime
from panda import Panda

# ============================================================================
# CONFIGURATION
# ============================================================================
WRITE_BUS = int(os.getenv("WRITE_BUS", "0"))  # Bus ID for writing (0=CAN1, 1=CAN2, 2=CAN3)
BUS_SPEED = int(os.getenv("WRITE_SPEED", "500"))  # Comfort CAN speed: 500kbps
print(f"WRITE_BUS: {WRITE_BUS}")
print(f"WRITE_SPEED: {BUS_SPEED}")

# ============================================================================
# CAN IDs (from C++ code)
# ============================================================================
ID_61A = 0x61A  # SOC & drive mode lever
ID_52D = 0x52D  # Estimated range
ID_65F = 0x65F  # VIN (3 parts)
ID_65D = 0x65D  # Odometer
ID_320 = 0x320  # Speed
ID_527 = 0x527  # Outdoor temperature
ID_381 = 0x381  # Vehicle locked
ID_3E3 = 0x3E3  # Cabin temperature
ID_470 = 0x470  # Doors
ID_531 = 0x531  # Headlights
ID_3E1 = 0x3E1  # HVAC running
ID_571 = 0x571  # 12V voltage
ID_61C = 0x61C  # Charge detection
ID_575 = 0x575  # Key position
ID_400 = 0x400  # Ring messages
ID_40C = 0x40C  # Ring messages
ID_436 = 0x436  # Ring messages
ID_439 = 0x439  # Ring messages
ID_43D = 0x43D  # Ring response (we send)
ID_69E = 0x69E  # Profile0 read/write
ID_69C = 0x69C  # Profile0 response
ID_5A9 = 0x5A9  # OCU heartbeat 1
ID_5A7 = 0x5A7  # OCU heartbeat 2

# ============================================================================
# GLOBAL STATE
# ============================================================================
class VehicleState:
    def __init__(self):
        # Vehicle info
        self.vin = ""
        self.vin_part1 = False
        self.vin_part2 = False
        self.vin_part3 = False

        # Battery & charging
        self.soc = 0.0
        self.range_ideal = 0.0
        self.range_est = 0.0
        self.charge_inprogress = False
        self.chargeport_door = False
        self.charge_pilot = False
        self.charge_climit = 0  # Current limit in A

        # Vehicle status
        self.speed = 0.0
        self.odometer = 0.0
        self.outdoor_temp = 0.0
        self.cabin_temp = 0.0
        self.locked = True
        self.headlights = False
        self.hvac = False
        self.car_on = False
        self.awake = False

        # Doors
        self.door_fl = False
        self.door_fr = False
        self.door_rl = False
        self.door_rr = False
        self.door_trunk = False
        self.door_hood = False

        # Drive mode lever (P=0x20, R=0x30, N=0x40, D=0x50, B=0x60)
        self.lever = 0x20

        # 12V battery
        self.bat_12v_voltage = 0.0
        self.charging_12v = False
        self.aux_12v = False

        # OCU/Ring state
        self.ocu_awake = False
        self.ocu_working = False
        self.ocu_response = False
        self.ring_awake = False

        # Profile0 (charging/climate settings)
        self.profile0 = [0] * 43
        self.profile0_recv = False
        self.profile0_mode = 0
        self.profile0_charge_current = 0
        self.profile0_cc_temp = 0  # Temperature in °C
        self.profile0_cc_onbat = False

        # Message tracking
        self.last_message_time = {}
        self.message_counts = {}

        # Control state
        self.cc_on = False

        # Ring response
        self.ring_response_needed = False
        self.ring_response_data = None

        self.lock = threading.Lock()

state = VehicleState()
keep_running = True

# ============================================================================
# DECODERS (from C++ IncomingFrameCan3)
# ============================================================================

def decode_61A_soc(data, bus):
    """ID 0x61A: SOC (byte 7) & drive mode lever (byte 3)"""
    if len(data) >= 8:
        with state.lock:
            state.soc = data[7] / 2.0
            # Model year detection would go here (2020+ vs pre-2020)
            # For now, assume 2020+ (260km WLTP)
            state.range_ideal = (260 * state.soc) / 100.0

            if state.lever != data[3]:
                print(f"[Bus {bus}] Drive lever: 0x{data[3]:02X}")
                state.lever = data[3]

def decode_52D_range(data, bus):
    """ID 0x52D: Estimated range"""
    if len(data) >= 2:
        with state.lock:
            if data[0] != 0xFE:
                if data[1] == 0x41:
                    state.range_est = data[0] + 255
                else:
                    state.range_est = data[0]

def decode_65F_vin(data, bus):
    """ID 0x65F: VIN (3 parts)"""
    with state.lock:
        if data[0] == 0x00:  # Part 1
            state.vin = chr(data[5]) + chr(data[6]) + chr(data[7])
            state.vin_part1 = True
        elif data[0] == 0x01 and state.vin_part1:  # Part 2
            state.vin += chr(data[1]) + chr(data[2]) + chr(data[3]) + chr(data[4]) + chr(data[5]) + chr(data[6]) + chr(data[7])
            state.vin_part2 = True
        elif data[0] == 0x02 and state.vin_part2 and not state.vin_part3:  # Part 3
            state.vin += chr(data[1]) + chr(data[2]) + chr(data[3]) + chr(data[4]) + chr(data[5]) + chr(data[6]) + chr(data[7])
            state.vin_part3 = True
            print(f"[Bus {bus}] VIN: {state.vin}")

def decode_65D_odo(data, bus):
    """ID 0x65D: Odometer"""
    if len(data) >= 4:
        with state.lock:
            state.odometer = float(((data[3] & 0xF) << 16) | (data[2] << 8) | data[1])

def decode_320_speed(data, bus):
    """ID 0x320: Speed"""
    if len(data) >= 5:
        with state.lock:
            state.speed = ((data[4] << 8) + data[3] - 1) / 190.0

def decode_527_temp(data, bus):
    """ID 0x527: Outdoor temperature"""
    if len(data) >= 6:
        with state.lock:
            state.outdoor_temp = (data[5] - 100) / 2.0

def decode_381_locked(data, bus):
    """ID 0x381: Vehicle locked"""
    if len(data) >= 1:
        with state.lock:
            state.locked = (data[0] == 0x02)

def decode_3E3_cabin_temp(data, bus):
    """ID 0x3E3: Cabin temperature"""
    if len(data) >= 3 and data[2] != 0xFF:
        with state.lock:
            state.cabin_temp = (data[2] - 100) / 2.0

def decode_470_doors(data, bus):
    """ID 0x470: Doors"""
    if len(data) >= 2:
        with state.lock:
            val = data[1]
            state.door_fl = (val & 0x01) > 0
            state.door_fr = (val & 0x02) > 0
            state.door_rl = (val & 0x04) > 0
            state.door_rr = (val & 0x08) > 0
            state.door_trunk = (val & 0x20) > 0
            state.door_hood = (val & 0x10) > 0

def decode_531_headlights(data, bus):
    """ID 0x531: Headlights"""
    if len(data) >= 1:
        with state.lock:
            state.headlights = (data[0] > 0)

def decode_3E1_hvac(data, bus):
    """ID 0x3E1: HVAC running"""
    if len(data) >= 5:
        with state.lock:
            state.hvac = (data[4] > 0)
            state.cc_on = state.hvac

def decode_571_12v(data, bus):
    """ID 0x571: 12V voltage"""
    if len(data) >= 1:
        with state.lock:
            state.bat_12v_voltage = 5 + (0.05 * data[0])

def decode_61C_charge(data, bus):
    """ID 0x61C: Charge detection"""
    if len(data) >= 3:
        with state.lock:
            # Byte 1: 0xF0 = Unplugged/Idle, others = Connected
            # Byte 2: 0x07 or 0x0F = Not charging, < 7 = Charging
            is_charging = not (data[1] == 0xF0 and (data[2] == 0x07 or data[2] == 0x0F))

            if is_charging != state.charge_inprogress:
                state.charge_inprogress = is_charging
                state.charge_pilot = is_charging
                state.chargeport_door = is_charging
                state.charging_12v = is_charging
                state.aux_12v = is_charging
                print(f"[Bus {bus}] Charge: {'STARTED' if is_charging else 'STOPPED'}")

def decode_575_key(data, bus):
    """ID 0x575: Key position"""
    if len(data) >= 1:
        with state.lock:
            key_pos = data[0]
            if key_pos == 0x00:  # No key
                state.car_on = False
                state.awake = False
            elif key_pos == 0x01:  # Key position 1
                state.awake = True
                state.car_on = False
            elif key_pos == 0x07:  # Key position 2, ignition on
                state.car_on = True
                state.awake = True

def decode_ring_messages(data, bus):
    """IDs 0x400, 0x40C, 0x436, 0x439: Ring messages"""
    if len(data) >= 2:
        with state.lock:
            if data[0] == 0x00 and data[1] != 0x31:
                if not state.ring_awake:
                    state.ring_awake = True
                    print(f"[Bus {bus}] Ring: AWAKE")
            elif data[1] == 0x31:  # Sleep command
                if state.ocu_awake:
                    print(f"[Bus {bus}] Ring: SLEEP requested")
                    state.ocu_awake = False
                    state.ocu_working = False
                    state.ocu_response = False
                elif state.ring_awake:
                    state.ring_awake = False
                    print(f"[Bus {bus}] Ring: ASLEEP")
            elif data[0] == 0x1D:  # Called in ring
                state.ocu_awake = True
                # Response handled in receive thread

def decode_69C_profile0(data, bus):
    """ID 0x69C: Profile0 response"""
    if len(data) >= 8:
        header = data[0]
        # Check for profile0 channel header (0x80-0xB0 with channel ID in d[4]==0x27)
        if (header & 0xF0) in [0x80, 0x90, 0xA0, 0xB0] and data[4] == 0x27 and not state.profile0_recv:
            channel = (header & 0x7F) >> 4
            print(f"[Bus {bus}] Profile0: Channel {channel} detected")
            state.profile0_recv = True
            state.profile0[0:8] = data[:8]
        elif state.profile0_recv:
            # Continuation frames (D0-D3)
            if (header & 0xF0) == 0xC0:  # D0
                state.profile0[8:16] = data[1:9] if len(data) >= 9 else data[1:8] + [0]
            elif (header & 0xF0) == 0xD0:  # D0 (alternative)
                state.profile0[11:19] = data[1:9] if len(data) >= 9 else data[1:8] + [0]
            elif (header & 0xF0) == 0xD1:  # D1
                state.profile0[19:27] = data[1:9] if len(data) >= 9 else data[1:8] + [0]
            elif (header & 0xF0) == 0xD2:  # D2
                state.profile0[27:35] = data[1:9] if len(data) >= 9 else data[1:8] + [0]
            elif (header & 0xF0) == 0xD3:  # D3
                state.profile0[35:43] = data[1:9] if len(data) >= 9 else data[1:8] + [0]
                # Profile complete
                if state.profile0[28] != 0 and state.profile0[29] != 0:
                    state.profile0_mode = state.profile0[11]
                    state.profile0_charge_current = state.profile0[13]
                    state.profile0_cc_temp = (state.profile0[25] / 10.0) + 10.0
                    state.profile0_cc_onbat = bool(state.profile0_mode & 4)
                    print(f"[Bus {bus}] Profile0: Mode={state.profile0_mode}, Current={state.profile0_charge_current}A, Temp={state.profile0_cc_temp:.1f}°C")

# Message decoder map
DECODERS = {
    ID_61A: decode_61A_soc,
    ID_52D: decode_52D_range,
    ID_65F: decode_65F_vin,
    ID_65D: decode_65D_odo,
    ID_320: decode_320_speed,
    ID_527: decode_527_temp,
    ID_381: decode_381_locked,
    ID_3E3: decode_3E3_cabin_temp,
    ID_470: decode_470_doors,
    ID_531: decode_531_headlights,
    ID_3E1: decode_3E1_hvac,
    ID_571: decode_571_12v,
    ID_61C: decode_61C_charge,
    ID_575: decode_575_key,
    ID_400: decode_ring_messages,
    ID_40C: decode_ring_messages,
    ID_436: decode_ring_messages,
    ID_439: decode_ring_messages,
    ID_69C: decode_69C_profile0,
}

# ============================================================================
# CONTROL FUNCTIONS (from C++ code)
# ============================================================================

def wakeup_t26(panda):
    """Wakeup sequence (WakeupT26Stage2)"""
    try:
        # Step 1: Request time (PID 451) to wake 0x400
        panda.can_send(ID_69E, struct.pack("BB", 0x14, 0x51), WRITE_BUS)
        time.sleep(0.02)

        # Step 2: Call ourselves in ring
        panda.can_send(ID_43D, struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00), WRITE_BUS)
        time.sleep(0.05)

        # Step 3: Talk to 0x400 to get accepted in ring
        panda.can_send(ID_43D, struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00), WRITE_BUS)

        with state.lock:
            state.ocu_awake = True
            state.ocu_working = True
            state.charging_12v = True
            state.aux_12v = True

        print(f"[TX Bus {WRITE_BUS}] Wakeup sequence sent")
        return True
    except Exception as e:
        print(f"[TX Bus {WRITE_BUS}] Wakeup failed: {e}")
        return False

def send_ocu_heartbeat(panda):
    """Send OCU heartbeat (SendOcuHeartbeat)"""
    try:
        # Heartbeat 1 (0x5A9)
        panda.can_send(ID_5A9, b'\x00' * 8, WRITE_BUS)

        # Heartbeat 2 (0x5A7)
        # Byte 0: 0x60 if working, 0x00 if ready to sleep
        b0 = 0x60 if state.ocu_working else 0x00
        panda.can_send(ID_5A7, struct.pack("BB", b0, 0x16) + b'\x00' * 6, WRITE_BUS)
        return True
    except Exception as e:
        print(f"[TX Bus {WRITE_BUS}] Heartbeat failed: {e}")
        return False

def request_profile0(panda):
    """Request Profile0 read (RequestProfile0)"""
    try:
        msg = struct.pack("BBBBBBBB", 0x90, 0x04, 0x19, 0x59, 0x27, 0x00, 0x00, 0x01)
        panda.can_send(ID_69E, msg, WRITE_BUS)
        with state.lock:
            state.profile0_recv = False
        print(f"[TX Bus {WRITE_BUS}] Profile0 read requested")
        return True
    except Exception as e:
        print(f"[TX Bus {WRITE_BUS}] Profile0 request failed: {e}")
        return False

def write_profile0(panda, mode=None, current=None, temp=None):
    """Write Profile0 (WriteProfile0)"""
    try:
        with state.lock:
            profile = state.profile0.copy()
            if mode is not None:
                profile[11] = mode
            if current is not None:
                profile[13] = current
            if temp is not None:
                profile[25] = int((temp - 10) * 10)

        # Header
        hdr = struct.pack("BBBBBBBB", 0x90, 0x20, 0x29, 0x59, 0x2C, 0x00, 0x00, 0x01)
        panda.can_send(ID_69E, hdr, WRITE_BUS)
        time.sleep(0.02)

        # D0
        d0 = struct.pack("BBBBBBBB", 0xD0, profile[11], profile[12], profile[13],
                         profile[14], profile[15], profile[17], profile[18])
        panda.can_send(ID_69E, d0, WRITE_BUS)
        time.sleep(0.01)

        # D1
        d1 = struct.pack("BBBBBBBB", 0xD1, profile[19], profile[20], profile[21],
                         profile[22], profile[23], profile[25], profile[26])
        panda.can_send(ID_69E, d1, WRITE_BUS)
        time.sleep(0.01)

        # D2
        d2 = struct.pack("BBBBBBBB", 0xD2, profile[27], profile[28], profile[29],
                         profile[30], profile[31], profile[33], profile[34])
        panda.can_send(ID_69E, d2, WRITE_BUS)
        time.sleep(0.01)

        # D3
        d3 = struct.pack("BBBBBBBB", 0xD3, profile[35], profile[36], profile[37],
                         profile[38], profile[39], profile[41], profile[42])
        panda.can_send(ID_69E, d3, WRITE_BUS)

        print(f"[TX Bus {WRITE_BUS}] Profile0 write sent")
        return True
    except Exception as e:
        print(f"[TX Bus {WRITE_BUS}] Profile0 write failed: {e}")
        return False

def activate_profile0(panda, activate):
    """Activate Profile0 (ActivateProfile0) - for charge/climate control"""
    try:
        msg = struct.pack("BBBB", 0x29, 0x58, 0x00, 1 if activate else 0)
        panda.can_send(ID_69E, msg, WRITE_BUS)
        print(f"[TX Bus {WRITE_BUS}] Profile0 activate: {activate}")
        return True
    except Exception as e:
        print(f"[TX Bus {WRITE_BUS}] Profile0 activate failed: {e}")
        return False

def command_climate_control(panda, on, temp_c=21.0):
    """Command climate control (CommandClimateControl)"""
    if not state.ocu_awake:
        wakeup_t26(panda)
        time.sleep(0.5)

    # Read profile first if needed
    if not state.profile0_recv:
        request_profile0(panda)
        time.sleep(0.5)

    # Set mode: bit 1 = charge, bit 2 = climate control
    mode = 2  # Climate control only
    if state.charge_inprogress:
        mode |= 1  # Also charge
    if state.profile0_cc_onbat:
        mode |= 4  # On battery

    # Set temperature
    write_profile0(panda, mode=mode, temp=temp_c)
    time.sleep(0.2)

    # Activate
    activate_profile0(panda, on)
    return True

def command_set_charge_current(panda, current_amps):
    """Set charge current limit (CommandSetChargeCurrent)"""
    if not state.ocu_awake:
        wakeup_t26(panda)
        time.sleep(0.5)

    if not state.profile0_recv:
        request_profile0(panda)
        time.sleep(0.5)

    # Clamp to valid range (0-32A for 2020+, 0-16A for pre-2020)
    current_amps = max(0, min(32, current_amps))

    write_profile0(panda, current=current_amps)
    return True

# ============================================================================
# MAIN LOOPS
# ============================================================================

def can_receive_thread(panda):
    """Background thread to receive CAN messages"""
    global keep_running

    while keep_running:
        try:
            incoming = panda.can_recv()
            for addr, data, bus in incoming:
                # Track message counts
                with state.lock:
                    key = f"{addr:03X}_bus{bus}"
                    state.message_counts[key] = state.message_counts.get(key, 0) + 1
                    state.last_message_time[key] = time.time()

                # Handle ring response if needed
                if addr in [ID_400, ID_40C, ID_436, ID_439] and len(data) >= 1 and data[0] == 0x1D:
                    # Respond to ring call
                    try:
                        with state.lock:
                            ocu_working = state.ocu_working
                            response = struct.pack("8B", 0x00, 0x01 if ocu_working else 0x11, 0x02, data[3] if len(data) > 3 else 0x00, 0x00, 0x14, 0x00, 0x00)
                        panda.can_send(ID_43D, response, WRITE_BUS)
                        print(f"[TX Bus {WRITE_BUS}] Ring response sent")
                    except Exception as e:
                        print(f"[TX Bus {WRITE_BUS}] Ring response failed: {e}")

                # Decode known messages
                if addr in DECODERS:
                    try:
                        DECODERS[addr](data, bus)
                    except Exception as e:
                        print(f"[Bus {bus}] Decode error for 0x{addr:03X}: {e}")
                else:
                    # Unknown message - show hex dump
                    hex_data = ' '.join(f'{b:02X}' for b in data)
                    print(f"[Bus {bus}] Unknown 0x{addr:03X}: {hex_data}")

        except Exception as e:
            print(f"CAN receive error: {e}")
            time.sleep(0.1)

        time.sleep(0.01)

def heartbeat_thread(panda):
    """Background thread for OCU heartbeat"""
    global keep_running

    while keep_running:
        if state.ocu_awake:
            send_ocu_heartbeat(panda)
        time.sleep(1.0)

def print_dashboard():
    """Print dashboard with all vehicle information"""
    os.system('clear' if os.name != 'nt' else 'cls')

    with state.lock:
        print("=" * 80)
        print("VW e-UP! Remote Control Dashboard")
        print(f"Write Bus: {WRITE_BUS} | Bus Speed: {BUS_SPEED}kbps")
        print("=" * 80)
        print()

        print("VEHICLE INFORMATION:")
        print(f"  VIN:              {state.vin if state.vin else 'Not received'}")
        print(f"  Speed:            {state.speed:.1f} km/h")
        print(f"  Odometer:         {state.odometer:.0f} km")
        print(f"  Drive Lever:      0x{state.lever:02X} ({'P' if state.lever==0x20 else 'R' if state.lever==0x30 else 'N' if state.lever==0x40 else 'D' if state.lever==0x50 else 'B' if state.lever==0x60 else '?'})")
        print()

        print("BATTERY & CHARGING:")
        print(f"  SOC:              {state.soc:.1f} %")
        print(f"  Range (Ideal):   {state.range_ideal:.1f} km")
        print(f"  Range (Est):     {state.range_est:.0f} km")
        print(f"  Charging:         {'YES' if state.charge_inprogress else 'NO'}")
        print(f"  Charge Port:     {'OPEN' if state.chargeport_door else 'CLOSED'}")
        print(f"  Charge Limit:    {state.profile0_charge_current} A")
        print(f"  12V Battery:      {state.bat_12v_voltage:.2f} V")
        print(f"  12V Charging:     {'YES' if state.charging_12v else 'NO'}")
        print()

        print("CLIMATE CONTROL:")
        print(f"  HVAC Running:    {'YES' if state.hvac else 'NO'}")
        print(f"  Target Temp:     {state.profile0_cc_temp:.1f} °C")
        print(f"  Cabin Temp:      {state.cabin_temp:.1f} °C")
        print(f"  Outdoor Temp:    {state.outdoor_temp:.1f} °C")
        print(f"  On Battery:       {'YES' if state.profile0_cc_onbat else 'NO'}")
        print()

        print("VEHICLE STATUS:")
        print(f"  Car On:          {'YES' if state.car_on else 'NO'}")
        print(f"  Awake:           {'YES' if state.awake else 'NO'}")
        print(f"  Locked:          {'YES' if state.locked else 'NO'}")
        print(f"  Headlights:      {'ON' if state.headlights else 'OFF'}")
        print()

        print("DOORS:")
        doors = []
        if state.door_fl: doors.append("FL")
        if state.door_fr: doors.append("FR")
        if state.door_rl: doors.append("RL")
        if state.door_rr: doors.append("RR")
        if state.door_trunk: doors.append("Trunk")
        if state.door_hood: doors.append("Hood")
        print(f"  Open:            {' '.join(doors) if doors else 'All Closed'}")
        print()

        print("OCU/RING STATE:")
        print(f"  OCU Awake:       {'YES' if state.ocu_awake else 'NO'}")
        print(f"  OCU Working:     {'YES' if state.ocu_working else 'NO'}")
        print(f"  Ring Awake:      {'YES' if state.ring_awake else 'NO'}")
        print(f"  Profile0 Loaded: {'YES' if state.profile0_recv else 'NO'}")
        print()

        print("CONTROLS:")
        print("  [w] Wakeup T26")
        print("  [r] Read Profile0")
        print("  [a] AC ON")
        print("  [s] AC OFF")
        print("  [t] Set AC Temp (21/24°C toggle)")
        print("  [c <amps>] Set Charge Current (e.g., 'c 16' for 16A)")
        print("  [q] Quit")
        print()
        print(f"Note: Write bus ID is {WRITE_BUS}. Set WRITE_BUS env var to change.")
        print()

        print("RECENT MESSAGES (last 10):")
        recent = sorted(state.last_message_time.items(), key=lambda x: x[1], reverse=True)[:10]
        for key, t in recent:
            count = state.message_counts.get(key, 0)
            age = time.time() - t
            print(f"  {key}: {count} msgs, {age:.1f}s ago")
        print("=" * 80)

def main():
    global keep_running

    print("Initializing Panda...")
    try:
        p = Panda()
        p.set_can_speed_kbps(WRITE_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
        print(f"Connected to Panda. Write bus: {WRITE_BUS}, Speed: {BUS_SPEED}kbps")
        print("Starting threads...")
    except Exception as e:
        print(f"Error connecting to Panda: {e}")
        return

    # Start background threads
    recv_thread = threading.Thread(target=can_receive_thread, args=(p,), daemon=True)
    recv_thread.start()

    hb_thread = threading.Thread(target=heartbeat_thread, args=(p,), daemon=True)
    hb_thread.start()

    # Command queue for thread-safe command handling
    import queue
    cmd_queue = queue.Queue()

    def input_handler():
        """Handle input in separate thread"""
        global keep_running
        while keep_running:
            try:
                cmd = input().strip().lower()
                cmd_queue.put(cmd)
            except (EOFError, KeyboardInterrupt):
                keep_running = False
                break

    input_thread = threading.Thread(target=input_handler, daemon=True)
    input_thread.start()

    # Main loop
    last_dashboard_time = 0
    dashboard_interval = 0.5  # Update dashboard every 0.5 seconds

    print("\nDashboard starting... Press 'q' to quit.\n")

    try:
        while keep_running:
            current_time = time.time()

            # Update dashboard periodically
            if current_time - last_dashboard_time >= dashboard_interval:
                print_dashboard()
                last_dashboard_time = current_time

            # Process commands (non-blocking)
            try:
                cmd = cmd_queue.get_nowait()
                if cmd == 'q':
                    break
                elif cmd == 'w':
                    wakeup_t26(p)
                elif cmd == 'r':
                    request_profile0(p)
                elif cmd == 'a':
                    command_climate_control(p, True)
                elif cmd == 's':
                    command_climate_control(p, False)
                elif cmd == 't':
                    new_temp = 24.0 if state.profile0_cc_temp < 23 else 21.0
                    command_climate_control(p, True, new_temp)
                elif cmd.startswith('c'):
                    # Allow 'c' alone or 'c <number>'
                    parts = cmd.split()
                    if len(parts) == 2:
                        try:
                            current = int(parts[1])
                            command_set_charge_current(p, current)
                        except ValueError:
                            print("Invalid charge current value")
                    else:
                        print("Usage: c <current_in_amps>")
                else:
                    print(f"Unknown command: {cmd}")
            except queue.Empty:
                pass

            time.sleep(0.1)

    except KeyboardInterrupt:
        pass
    finally:
        keep_running = False
        time.sleep(0.2)  # Let threads finish
        try:
            p.set_safety_mode(Panda.SAFETY_SILENT)
        except:
            pass
        print("\nExited.")

if __name__ == "__main__":
    main()
