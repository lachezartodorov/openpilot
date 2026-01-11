#!/usr/bin/env python3
"""
OMEGA - VW e-UP! Remote Control Tool
=====================================
Ported from OVMS vweup_t26.cpp for Comma/Panda communication.

Features:
- Listens to all CAN buses (0, 1, 2)
- Decodes all VW e-UP! CAN messages
- Remote AC start/stop
- Charge current limit control
- Climate temperature control
- Charge start/stop
- Live dashboard with all vehicle data

Usage:
  python omega.py [--write-bus N] [--speed N]

Controls:
  1 - Turn AC ON
  0 - Turn AC OFF
  + - Increase charge current limit
  - - Decrease charge current limit
  c - Start charging
  s - Stop charging
  w - Wake up vehicle
  r - Read profile0 (charge/climate settings)
  t - Toggle climate temperature (21°C / 24°C)
  q - Quit
"""

import time
import struct
import threading
import argparse
import sys
from datetime import datetime
from collections import defaultdict
from panda import Panda

# =============================================================================
# CONFIGURATION
# =============================================================================
DEFAULT_WRITE_BUS = 0        # Bus to write commands to (0=CAN1, 1=CAN2, 2=CAN3)
DEFAULT_BUS_SPEED = 500      # 100kbps for Comfort CAN (T26)

# Climate control temperature limits (from OVMS)
CC_TEMP_MIN = 16
CC_TEMP_MAX = 30

# =============================================================================
# CAN MESSAGE IDs (from OVMS vweup_t26.cpp)
# =============================================================================
# Read IDs (Vehicle -> Controller)
ID_SOC_LEVER      = 0x61A    # SOC (byte 7) & drive mode lever (byte 3)
ID_RANGE_EST      = 0x52D    # Estimated range
ID_VIN            = 0x65F    # VIN (3 parts)
ID_ODO            = 0x65D    # Odometer
ID_SPEED          = 0x320    # Speed
ID_TEMP_OUT       = 0x527    # Outdoor temperature
ID_LOCKED         = 0x381    # Vehicle locked
ID_TEMP_CABIN     = 0x3E3    # Cabin temperature
ID_DOORS          = 0x470    # Doors status
ID_HEADLIGHTS     = 0x531    # Headlights
ID_HVAC           = 0x3E1    # HVAC status
ID_12V            = 0x571    # 12V battery voltage
ID_CHARGE         = 0x61C    # Charge detection
ID_KEY            = 0x575    # Key position
ID_OCU_RESP       = 0x69C    # OCU response / Profile0 data

# Ring bus IDs (for wakeup/keep-alive)
ID_RING_ILM       = 0x400    # Welcome to ring from ILM
ID_RING_CLIMA     = 0x40C    # Climatronic
ID_RING_436       = 0x436    # Ring working
ID_RING_439       = 0x439    # Ring working (alternate)

# Write IDs (Controller -> Vehicle)
ID_HEARTBEAT_1    = 0x5A9    # OCU Heartbeat 1
ID_HEARTBEAT_2    = 0x5A7    # OCU Heartbeat 2
ID_RING_CMD       = 0x43D    # Ring communication
ID_CMD            = 0x69E    # Commands (profile R/W, climate, charge)

# =============================================================================
# GLOBAL STATE
# =============================================================================
class VehicleState:
    def __init__(self):
        # Vehicle info
        self.vin = ""
        self.vin_parts = [False, False, False]
        self.vin_data = [0] * 18

        # Battery & range
        self.soc = 0.0
        self.range_est = 0
        self.range_ideal = 0.0
        self.battery_12v = 0.0
        self.battery_12v_hw = 0.0  # From Panda hardware

        # Temperatures
        self.temp_outdoor = 0.0
        self.temp_cabin = 0.0
        self.cc_temp_target = 20  # Climate control target temp

        # Status
        self.speed = 0.0
        self.odometer = 0
        self.locked = True
        self.headlights = False
        self.hvac_on = False
        self.charging = False
        self.charge_pilot = False
        self.car_on = False
        self.awake = False

        # Doors
        self.door_fl = False
        self.door_fr = False
        self.door_rl = False
        self.door_rr = False
        self.door_trunk = False
        self.door_hood = False

        # Key position
        self.key_position = 0x00
        self.key_text = "No Key"

        # Lever position
        self.lever = 0x20  # Assume P on startup
        self.lever_text = "P"

        # OCU/Ring state
        self.ocu_awake = False
        self.ocu_working = False
        self.ocu_response = False
        self.ring_awake = False

        # Profile0 data (charge/climate settings)
        self.profile0 = [0] * 48
        self.profile0_loaded = False
        self.profile0_mode = 0
        self.profile0_charge_current = 0
        self.profile0_cc_temp = 20
        self.climit_max = 32  # 32A for 2020+ models, 16A for older

        # Charging state
        self.charge_count = 0
        self.last_charge_state = False

        # Message tracking
        self.last_msg_time = {}
        self.msg_count = defaultdict(int)
        self.bus_activity = {0: False, 1: False, 2: False}


state = VehicleState()
state_lock = threading.Lock()
keep_running = True

# Command queue
cmd_queue = []
cmd_lock = threading.Lock()

# =============================================================================
# CAN MESSAGE DECODERS (from OVMS vweup_t26.cpp)
# =============================================================================
def decode_soc_lever(data, bus):
    """0x61A: SOC (byte 7) & drive mode lever (byte 3)"""
    if len(data) < 8:
        return

    with state_lock:
        state.soc = data[7] / 2.0

        # Calculate ideal range based on SOC (dirty WLTP approximation)
        # 260km for 2020+ models, 160km for older
        state.range_ideal = (260 * state.soc) / 100.0

        # Lever position
        lever = data[3]
        if lever != state.lever:
            state.lever = lever
            lever_map = {
                0x20: "P",
                0x30: "R",
                0x40: "N",
                0x50: "D",
                0x60: "B",
                0x00: "?"
            }
            state.lever_text = lever_map.get(lever, f"0x{lever:02X}")


def decode_range_est(data, bus):
    """0x52D: Estimated range in km"""
    if len(data) < 2 or data[0] == 0xFE:
        return

    with state_lock:
        if data[1] == 0x41:
            state.range_est = data[0] + 255
        else:
            state.range_est = data[0]


def decode_vin(data, bus):
    """0x65F: VIN (3 parts)"""
    if len(data) < 8:
        return

    with state_lock:
        part = data[0]
        if part == 0x00:
            state.vin_data[0] = data[5]
            state.vin_data[1] = data[6]
            state.vin_data[2] = data[7]
            state.vin_parts[0] = True
        elif part == 0x01 and state.vin_parts[0]:
            state.vin_data[3:10] = data[1:8]
            state.vin_parts[1] = True
        elif part == 0x02 and state.vin_parts[1] and not state.vin_parts[2]:
            state.vin_data[10:17] = data[1:8]
            state.vin_parts[2] = True
            try:
                state.vin = bytes(state.vin_data[:17]).decode('ascii', errors='ignore')
            except:
                state.vin = "DECODE_ERROR"


def decode_odo(data, bus):
    """0x65D: Odometer"""
    if len(data) < 4:
        return

    with state_lock:
        odo = ((data[3] & 0x0F) << 16) | (data[2] << 8) | data[1]
        state.odometer = odo


def decode_speed(data, bus):
    """0x320: Speed in km/h"""
    if len(data) < 5:
        return

    with state_lock:
        state.speed = ((data[4] << 8) + data[3] - 1) / 190.0
        if state.speed < 0:
            state.speed = 0


def decode_temp_outdoor(data, bus):
    """0x527: Outdoor temperature"""
    if len(data) < 6:
        return

    with state_lock:
        state.temp_outdoor = (data[5] - 100) / 2.0


def decode_locked(data, bus):
    """0x381: Vehicle locked status"""
    if len(data) < 1:
        return

    with state_lock:
        state.locked = (data[0] == 0x02)


def decode_temp_cabin(data, bus):
    """0x3E3: Cabin temperature"""
    if len(data) < 3 or data[2] == 0xFF:
        return

    with state_lock:
        state.temp_cabin = (data[2] - 100) / 2.0


def decode_doors(data, bus):
    """0x470: Door status"""
    if len(data) < 2:
        return

    with state_lock:
        state.door_fl = bool(data[1] & 0x01)
        state.door_fr = bool(data[1] & 0x02)
        state.door_rl = bool(data[1] & 0x04)
        state.door_rr = bool(data[1] & 0x08)
        state.door_hood = bool(data[1] & 0x10)
        state.door_trunk = bool(data[1] & 0x20)


def decode_headlights(data, bus):
    """0x531: Headlights status"""
    if len(data) < 1:
        return

    with state_lock:
        state.headlights = (data[0] > 0)


def decode_hvac(data, bus):
    """0x3E1: HVAC status"""
    if len(data) < 5:
        return

    with state_lock:
        state.hvac_on = (data[4] > 0)


def decode_12v(data, bus):
    """0x571: 12V battery voltage"""
    if len(data) < 1:
        return

    with state_lock:
        state.battery_12v = 5.0 + (0.05 * data[0])


def decode_charge(data, bus):
    """0x61C: Charge detection"""
    if len(data) < 3:
        return

    with state_lock:
        is_charging = not (data[1] == 0xF0 and (data[2] == 0x07 or data[2] == 0x0F))

        if is_charging != state.last_charge_state:
            state.charge_count += 1
            if state.charge_count >= 3:
                state.charge_count = 0
                state.charging = is_charging
                state.charge_pilot = is_charging
                state.last_charge_state = is_charging
        else:
            state.charge_count = 0


def decode_key(data, bus):
    """0x575: Key position"""
    if len(data) < 1:
        return

    with state_lock:
        state.key_position = data[0]
        key_map = {
            0x00: "No Key",
            0x01: "Pos 1 (ACC)",
            0x03: "Ignition Off",
            0x05: "Ignition On",
            0x07: "Pos 2 (ON)",
            0x0F: "Pos 3 (Start)"
        }
        state.key_text = key_map.get(data[0], f"0x{data[0]:02X}")

        # Update car_on and awake based on key
        if data[0] == 0x00:
            state.car_on = False
            state.awake = False
        elif data[0] == 0x01:
            state.awake = True
            state.car_on = False
        elif data[0] == 0x07:
            state.car_on = True
            state.awake = True


def decode_ocu_response(data, bus):
    """0x69C: OCU response / Profile0 data"""
    if len(data) < 8:
        return

    with state_lock:
        state.ocu_response = True

        # Check for profile0 response (channel headers)
        header = data[0]
        if header in (0x80, 0x90, 0xA0, 0xB0) and data[4] == 0x27:
            # Profile0 header received
            state.profile0[:8] = list(data)
            state.profile0_loaded = False
        elif state.profile0[0] != 0 and header in (0xC0, 0xD0, 0xE0, 0xF0):
            # Profile0 data continuation
            idx = ((header & 0x0F) * 8) + 8
            if idx + 8 <= len(state.profile0):
                state.profile0[idx:idx+8] = list(data)

            # Check if we have complete profile0 (ends with D3/E3/F3)
            if (header & 0x0F) == 3:
                # Parse profile0 values
                if len(state.profile0) > 29 and state.profile0[28] != 0:
                    state.profile0_loaded = True
                    state.profile0_mode = state.profile0[11]
                    state.profile0_charge_current = state.profile0[13]
                    state.profile0_cc_temp = (state.profile0[25] // 10) + 10


def decode_ring(data, bus):
    """0x400, 0x40C, 0x436, 0x439: Ring bus status"""
    if len(data) < 2:
        return

    with state_lock:
        if data[0] == 0x00 and data[1] != 0x31:
            state.ring_awake = True
        elif data[1] == 0x31:
            state.ring_awake = False
            state.ocu_awake = False
            state.ocu_working = False

        # Handle ring call (0x1D in first byte)
        if data[0] == 0x1D:
            state.ocu_awake = True


# Decoder mapping
DECODERS = {
    ID_SOC_LEVER: decode_soc_lever,
    ID_RANGE_EST: decode_range_est,
    ID_VIN: decode_vin,
    ID_ODO: decode_odo,
    ID_SPEED: decode_speed,
    ID_TEMP_OUT: decode_temp_outdoor,
    ID_LOCKED: decode_locked,
    ID_TEMP_CABIN: decode_temp_cabin,
    ID_DOORS: decode_doors,
    ID_HEADLIGHTS: decode_headlights,
    ID_HVAC: decode_hvac,
    ID_12V: decode_12v,
    ID_CHARGE: decode_charge,
    ID_KEY: decode_key,
    ID_OCU_RESP: decode_ocu_response,
    ID_RING_ILM: decode_ring,
    ID_RING_CLIMA: decode_ring,
    ID_RING_436: decode_ring,
    ID_RING_439: decode_ring,
}

# Message names for display
MSG_NAMES = {
    ID_SOC_LEVER: "SOC/Lever",
    ID_RANGE_EST: "Range Est",
    ID_VIN: "VIN",
    ID_ODO: "Odometer",
    ID_SPEED: "Speed",
    ID_TEMP_OUT: "Temp Out",
    ID_LOCKED: "Locked",
    ID_TEMP_CABIN: "Temp Cabin",
    ID_DOORS: "Doors",
    ID_HEADLIGHTS: "Headlights",
    ID_HVAC: "HVAC",
    ID_12V: "12V Battery",
    ID_CHARGE: "Charging",
    ID_KEY: "Key",
    ID_OCU_RESP: "OCU Resp",
    ID_RING_ILM: "Ring ILM",
    ID_RING_CLIMA: "Ring Clima",
    ID_RING_436: "Ring 436",
    ID_RING_439: "Ring 439",
    ID_HEARTBEAT_1: "HB1 (TX)",
    ID_HEARTBEAT_2: "HB2 (TX)",
    ID_RING_CMD: "Ring CMD",
    ID_CMD: "Command",
}

# =============================================================================
# CAN COMMANDS (from OVMS vweup_t26.cpp)
# =============================================================================
def cmd_wakeup(p, write_bus):
    """Wake up the Comfort CAN bus via ring protocol"""
    print("[CMD] Waking up vehicle...")

    # Send time request on 0x69E (wakes 0x400)
    p.can_send(ID_CMD, bytes([0x14, 0x51]), write_bus)
    time.sleep(0.02)

    # Call ourselves in the ring
    ring_msg = struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00)
    p.can_send(ID_RING_CMD, ring_msg, write_bus)
    time.sleep(0.06)

    # Answer to 0x400
    ring_answer = struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00)
    p.can_send(ID_RING_CMD, ring_answer, write_bus)

    with state_lock:
        state.ocu_awake = True
        state.ocu_working = True

    print("[CMD] Wakeup sequence sent")


def cmd_heartbeat(p, write_bus, active=False):
    """Send OCU heartbeat to keep bus awake"""
    # 0x5A9: All zeros
    p.can_send(ID_HEARTBEAT_1, bytes([0x00] * 8), write_bus)

    # 0x5A7: [0x60 if active else 0x00, 0x16, 0, 0, 0, 0, 0, 0]
    b0 = 0x60 if active else 0x00
    p.can_send(ID_HEARTBEAT_2, bytes([b0, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]), write_bus)


def cmd_request_profile0(p, write_bus):
    """Request Profile0 (charge/climate settings) from vehicle"""
    print("[CMD] Requesting Profile0...")
    # PID 959, channel 0x27, all parts, from index 0, 1 profile
    msg = struct.pack("8B", 0x90, 0x04, 0x19, 0x59, 0x27, 0x00, 0x00, 0x01)
    p.can_send(ID_CMD, msg, write_bus)


def cmd_write_profile0(p, write_bus, charge_current=None, cc_temp=None, mode=None):
    """Write Profile0 to vehicle"""
    with state_lock:
        if not state.profile0_loaded:
            print("[ERR] Profile0 not loaded! Read it first with 'r'")
            return

        profile = state.profile0.copy()

    if charge_current is not None:
        profile[13] = charge_current
        print(f"[CMD] Setting charge current to {charge_current}A")

    if cc_temp is not None:
        profile[25] = (cc_temp - 10) * 10
        print(f"[CMD] Setting CC temperature to {cc_temp}°C")

    if mode is not None:
        profile[11] = mode
        print(f"[CMD] Setting mode to {mode}")

    # Write header
    hdr = struct.pack("8B", 0x90, 0x20, 0x29, 0x59, 0x2C, 0x00, 0x00, 0x01)
    p.can_send(ID_CMD, hdr, write_bus)
    time.sleep(0.02)

    # D0
    d0 = struct.pack("8B", 0xD0, profile[11], profile[12], profile[13],
                     profile[14], profile[15], profile[17], profile[18])
    p.can_send(ID_CMD, d0, write_bus)
    time.sleep(0.01)

    # D1 (has temp at byte 6)
    d1 = struct.pack("8B", 0xD1, profile[19], profile[20], profile[21],
                     profile[22], profile[23], profile[25], profile[26])
    p.can_send(ID_CMD, d1, write_bus)
    time.sleep(0.01)

    # D2
    d2 = struct.pack("8B", 0xD2, profile[27], profile[28], profile[29],
                     profile[30], profile[31], profile[33], profile[34])
    p.can_send(ID_CMD, d2, write_bus)
    time.sleep(0.01)

    # D3
    d3 = struct.pack("8B", 0xD3, profile[35], profile[36], profile[37],
                     profile[38], profile[39], profile[41], profile[42])
    p.can_send(ID_CMD, d3, write_bus)

    print("[CMD] Profile0 write sent")


def cmd_activate_profile0(p, write_bus, activate=True):
    """Activate/deactivate profile0 (start/stop climate or charge)"""
    msg = struct.pack("4B", 0x29, 0x58, 0x00, 0x01 if activate else 0x00)
    p.can_send(ID_CMD, msg, write_bus)
    print(f"[CMD] Profile0 {'activated' if activate else 'deactivated'}")


def cmd_climate_control(p, write_bus, enable=True):
    """Start/stop climate control"""
    print(f"[CMD] Climate control {'ON' if enable else 'OFF'}")

    with state_lock:
        cc_onbat = bool(state.profile0_mode & 4) if state.profile0_loaded else False

    # Mode byte: 2 = CC on, 4 = CC on battery, 1 = charge on
    mode = 6 if cc_onbat else 2  # CC with/without battery
    if enable and state.charging:
        mode += 1  # Add charge bit if charging

    # Queue wakeup + profile write + activate
    with cmd_lock:
        cmd_queue.append(("wakeup", {}))
        cmd_queue.append(("write_profile0", {"mode": mode}))
        cmd_queue.append(("activate_profile0", {"activate": enable}))


def cmd_start_charge(p, write_bus):
    """Start charging"""
    print("[CMD] Starting charge...")

    with state_lock:
        cc_onbat = bool(state.profile0_mode & 4) if state.profile0_loaded else False
        hvac = state.hvac_on

    mode = 5 if cc_onbat else 1  # Charge with/without CC on battery
    if hvac:
        mode += 2  # Add HVAC bit

    with cmd_lock:
        cmd_queue.append(("wakeup", {}))
        cmd_queue.append(("write_profile0", {"mode": mode}))
        cmd_queue.append(("activate_profile0", {"activate": True}))


def cmd_stop_charge(p, write_bus):
    """Stop charging"""
    print("[CMD] Stopping charge...")

    with state_lock:
        cc_onbat = bool(state.profile0_mode & 4) if state.profile0_loaded else False
        hvac = state.hvac_on

    mode = 4 if cc_onbat else 0  # Stop charge, keep CC on battery setting
    if hvac:
        mode += 2

    with cmd_lock:
        cmd_queue.append(("wakeup", {}))
        cmd_queue.append(("write_profile0", {"mode": mode}))
        cmd_queue.append(("activate_profile0", {"activate": False}))


# =============================================================================
# BACKGROUND WORKER THREAD
# =============================================================================
def worker_thread(p, write_bus):
    """Background thread: receives CAN, sends heartbeats, processes commands"""
    global keep_running

    last_heartbeat = 0
    heartbeat_interval = 1.0
    fas_counter = 0
    transition_active = False

    while keep_running:
        current_time = time.time()

        # --- RECEIVE CAN FROM ALL BUSES ---
        try:
            incoming = p.can_recv()
            for addr, data, bus in incoming:
                with state_lock:
                    state.msg_count[addr] += 1
                    state.last_msg_time[addr] = current_time
                    state.bus_activity[bus] = True

                # Decode known messages
                if addr in DECODERS:
                    try:
                        DECODERS[addr](data, bus)
                    except Exception as e:
                        pass  # Ignore decode errors
        except Exception as e:
            print(f"[ERR] CAN receive error: {e}")

        # --- READ PANDA HARDWARE 12V ---
        try:
            health = p.health()
            if 'voltage' in health:
                with state_lock:
                    state.battery_12v_hw = health['voltage'] / 1000.0
        except:
            pass

        # --- SEND HEARTBEAT ---
        with state_lock:
            should_heartbeat = state.ocu_awake or state.ring_awake

        if should_heartbeat and current_time - last_heartbeat > heartbeat_interval:
            last_heartbeat = current_time
            try:
                cmd_heartbeat(p, write_bus, active=transition_active)
                if transition_active:
                    fas_counter += 1
                    if fas_counter >= 10:
                        transition_active = False
                        fas_counter = 0
            except Exception as e:
                print(f"[ERR] Heartbeat send error: {e}")

        # --- PROCESS COMMAND QUEUE ---
        with cmd_lock:
            if cmd_queue:
                cmd_name, cmd_args = cmd_queue.pop(0)
                transition_active = True
                fas_counter = 0

        if 'cmd_name' in dir() and cmd_name:
            try:
                if cmd_name == "wakeup":
                    cmd_wakeup(p, write_bus)
                elif cmd_name == "heartbeat":
                    cmd_heartbeat(p, write_bus, **cmd_args)
                elif cmd_name == "request_profile0":
                    cmd_request_profile0(p, write_bus)
                elif cmd_name == "write_profile0":
                    cmd_write_profile0(p, write_bus, **cmd_args)
                elif cmd_name == "activate_profile0":
                    cmd_activate_profile0(p, write_bus, **cmd_args)
                cmd_name = None
            except Exception as e:
                print(f"[ERR] Command error: {e}")
                cmd_name = None

        time.sleep(0.01)


# =============================================================================
# DISPLAY
# =============================================================================
def format_doors():
    """Format door status string"""
    with state_lock:
        doors = []
        if state.door_fl: doors.append("FL")
        if state.door_fr: doors.append("FR")
        if state.door_rl: doors.append("RL")
        if state.door_rr: doors.append("RR")
        if state.door_hood: doors.append("Hood")
        if state.door_trunk: doors.append("Trunk")
        return " ".join(doors) if doors else "All Closed"


def format_status():
    """Format vehicle status string"""
    with state_lock:
        status = []
        if state.car_on: status.append("ON")
        if state.charging: status.append("CHARGING")
        if state.hvac_on: status.append("HVAC")
        if state.locked: status.append("LOCKED")
        if state.headlights: status.append("LIGHTS")
        if state.ocu_awake: status.append("OCU")
        if state.ring_awake: status.append("RING")
        return " | ".join(status) if status else "OFF"


def print_dashboard():
    """Print the dashboard to console"""
    # Clear screen
    print('\033[H\033[J', end='')

    with state_lock:
        s = state

        print("=" * 70)
        print("  OMEGA - VW e-UP! Remote Control Tool")
        print("=" * 70)
        print()

        # Status line
        print(f"  STATUS: {format_status()}")
        print()

        # Vehicle info
        print(f"  VIN: {s.vin or 'Reading...'}")
        print(f"  Odometer: {s.odometer:,} km")
        print()

        # Battery & Range
        print("-" * 70)
        print("  BATTERY & RANGE")
        print("-" * 70)
        print(f"  SoC: {s.soc:.1f}%")
        print(f"  Range (Est): {s.range_est} km")
        print(f"  Range (Ideal): {s.range_ideal:.0f} km")
        print(f"  12V Battery: {s.battery_12v:.2f}V (CAN) / {s.battery_12v_hw:.2f}V (HW)")
        print()

        # Temperatures
        print("-" * 70)
        print("  TEMPERATURES")
        print("-" * 70)
        print(f"  Outdoor: {s.temp_outdoor:.1f}°C")
        print(f"  Cabin: {s.temp_cabin:.1f}°C")
        print(f"  CC Target: {s.profile0_cc_temp}°C")
        print()

        # Driving
        print("-" * 70)
        print("  DRIVING")
        print("-" * 70)
        print(f"  Speed: {s.speed:.1f} km/h")
        print(f"  Lever: {s.lever_text}")
        print(f"  Key: {s.key_text}")
        print()

        # Doors
        print("-" * 70)
        print("  DOORS")
        print("-" * 70)
        print(f"  {format_doors()}")
        print()

        # Profile0 / Charge settings
        print("-" * 70)
        print("  CHARGE/CLIMATE SETTINGS (Profile0)")
        print("-" * 70)
        if s.profile0_loaded:
            print(f"  Mode: 0x{s.profile0_mode:02X}")
            print(f"  Charge Current Limit: {s.profile0_charge_current}A (max: {s.climit_max}A)")
            print(f"  CC Temperature: {s.profile0_cc_temp}°C")
        else:
            print("  [Not loaded - press 'r' to read]")
        print()

        # Bus activity
        print("-" * 70)
        print("  BUS ACTIVITY")
        print("-" * 70)
        for bus_id in [0, 1, 2]:
            active = "ACTIVE" if s.bus_activity[bus_id] else "silent"
            print(f"  Bus {bus_id}: {active}")
        print()

        # Message counts
        print("-" * 70)
        print("  MESSAGE COUNTS (last 10)")
        print("-" * 70)
        sorted_msgs = sorted(s.msg_count.items(), key=lambda x: x[1], reverse=True)[:10]
        for msg_id, count in sorted_msgs:
            name = MSG_NAMES.get(msg_id, f"0x{msg_id:03X}")
            print(f"  0x{msg_id:03X} ({name}): {count}")
        print()

        # Controls
        print("=" * 70)
        print("  CONTROLS")
        print("=" * 70)
        print("  1 - AC ON      0 - AC OFF     w - Wakeup")
        print("  + - Current+   - - Current-   r - Read Profile")
        print("  c - Charge     s - Stop Chg   t - Toggle Temp (21/24°C)")
        print("  q - Quit")
        print("=" * 70)


# =============================================================================
# MAIN
# =============================================================================
def main():
    global keep_running

    parser = argparse.ArgumentParser(description='OMEGA - VW e-UP! Remote Control Tool')
    parser.add_argument('--write-bus', type=int, default=DEFAULT_WRITE_BUS,
                        help=f'CAN bus for writing commands (default: {DEFAULT_WRITE_BUS})')
    parser.add_argument('--speed', type=int, default=DEFAULT_BUS_SPEED,
                        help=f'CAN bus speed in kbps (default: {DEFAULT_BUS_SPEED})')
    args = parser.parse_args()

    write_bus = args.write_bus
    bus_speed = args.speed

    print(f"[*] OMEGA - VW e-UP! Remote Control Tool")
    print(f"[*] Connecting to Panda...")

    try:
        p = Panda()

        p.set_can_speed_kbps(0, 500)
        p.set_can_speed_kbps(1, 100)
        p.set_can_speed_kbps(2, 100)

        # Enable all output for commands
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        print(f"[*] Connected! Serial: {p.get_serial()}")

    except Exception as e:
        print(f"[!] Error connecting to Panda: {e}")
        print("[!] Hint: Did you run 'pkill -f pandad' first?")
        return 1

    # Start worker thread
    worker = threading.Thread(target=worker_thread, args=(p, write_bus))
    worker.start()

    print("[*] Worker thread started. Press any key to begin...")

    try:
        import termios
        import tty
        import select

        old_settings = termios.tcgetattr(sys.stdin)

        try:
            tty.setcbreak(sys.stdin.fileno())

            while keep_running:
                # Print dashboard
                print_dashboard()

                # Check for input (non-blocking)
                if select.select([sys.stdin], [], [], 0.5)[0]:
                    key = sys.stdin.read(1)

                    if key == 'q':
                        keep_running = False
                    elif key == '1':
                        cmd_climate_control(p, write_bus, enable=True)
                    elif key == '0':
                        cmd_climate_control(p, write_bus, enable=False)
                    elif key == 'w':
                        with cmd_lock:
                            cmd_queue.append(("wakeup", {}))
                    elif key == 'r':
                        with cmd_lock:
                            cmd_queue.append(("wakeup", {}))
                            cmd_queue.append(("request_profile0", {}))
                    elif key == 't':
                        # Toggle temp between 21 and 24
                        with state_lock:
                            current = state.profile0_cc_temp
                            new_temp = 24 if current < 23 else 21
                        with cmd_lock:
                            cmd_queue.append(("wakeup", {}))
                            cmd_queue.append(("write_profile0", {"cc_temp": new_temp}))
                    elif key == '+' or key == '=':
                        with state_lock:
                            current = state.profile0_charge_current
                            new_current = min(current + 2, state.climit_max)
                        with cmd_lock:
                            cmd_queue.append(("wakeup", {}))
                            cmd_queue.append(("write_profile0", {"charge_current": new_current}))
                    elif key == '-':
                        with state_lock:
                            current = state.profile0_charge_current
                            new_current = max(current - 2, 4)  # Min 4A for reliable charging
                        with cmd_lock:
                            cmd_queue.append(("wakeup", {}))
                            cmd_queue.append(("write_profile0", {"charge_current": new_current}))
                    elif key == 'c':
                        cmd_start_charge(p, write_bus)
                    elif key == 's':
                        cmd_stop_charge(p, write_bus)

        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    except ImportError:
        # Windows fallback - use input()
        print("[*] Running in input() mode (Windows)")
        while keep_running:
            print_dashboard()
            try:
                cmd = input("Command > ").strip().lower()
                if cmd == 'q':
                    keep_running = False
                elif cmd == '1':
                    cmd_climate_control(p, write_bus, enable=True)
                elif cmd == '0':
                    cmd_climate_control(p, write_bus, enable=False)
                elif cmd == 'w':
                    with cmd_lock:
                        cmd_queue.append(("wakeup", {}))
                elif cmd == 'r':
                    with cmd_lock:
                        cmd_queue.append(("wakeup", {}))
                        cmd_queue.append(("request_profile0", {}))
                elif cmd == 't':
                    with state_lock:
                        current = state.profile0_cc_temp
                        new_temp = 24 if current < 23 else 21
                    with cmd_lock:
                        cmd_queue.append(("wakeup", {}))
                        cmd_queue.append(("write_profile0", {"cc_temp": new_temp}))
                elif cmd == 'c':
                    cmd_start_charge(p, write_bus)
                elif cmd == 's':
                    cmd_stop_charge(p, write_bus)
            except EOFError:
                keep_running = False

    except KeyboardInterrupt:
        pass

    finally:
        print("\n[*] Shutting down...")
        keep_running = False
        worker.join(timeout=2.0)

        try:
            p.set_safety_mode(Panda.SAFETY_SILENT)
        except:
            pass

        print("[*] Goodbye!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
