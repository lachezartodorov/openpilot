import time
import struct
import threading
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 0         # 0=CAN1, 1=CAN2 (Comfort CAN)
BUS_SPEED  = 500       # 100kbps

# --- IDs ---
ID_POKE       = 0x69E  # Wakeup Poke
ID_RING       = 0x43D  # Ring Registration
ID_HEARTBEAT_1= 0x5A9  # Keep Alive (00s)
ID_HEARTBEAT_2= 0x5A7  # Status (16/60)
ID_AC_CMD     = 0x69E  # AC Command
ID_KNOB_READ  = 0x52D  # Temp Knob (Verified)

# --- GLOBAL STATE ---
keep_running = True
ac_request   = None
current_temp = 21.0
transition_active = False
fas_counter = 0
bus_awake = False      # Track if we see traffic
last_traffic_time = 0

def get_ac_command(enable=True, temp_c=21.0):
    cmd_byte = 0x80 if enable else 0x40
    temp_raw = int(temp_c * 2)
    return struct.pack("BBBBBBBB", cmd_byte, 0x04, temp_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

def perform_wakeup_handshake(p):
    """
    Robust Handshake with Traffic Logging.
    """
    global bus_awake, last_traffic_time
    print("\n[-] STARTING WAKE-UP SEQUENCE...")

    # Try up to 3 times to wake the car
    for attempt in range(1, 4):
        print(f" -> Attempt {attempt}/3: Sending Handshake...")

        # 1. The Poke (0x69E)
        try:
            p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)
        except Exception as e:
            print(f"    [!] TX Error: {e}")

        time.sleep(0.02) # SAFETY DELAY

        # 2. The Self-Call (0x43D)
        msg_self = struct.pack("BBBBBBBB", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00)
        p.can_send(ID_RING, msg_self, TARGET_BUS)

        time.sleep(0.06) # REQUIRED DELAY

        # 3. The Registration (0x43D)
        msg_reg = struct.pack("BBBBBBBB", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00)
        p.can_send(ID_RING, msg_reg, TARGET_BUS)

        print("    Handshake sent. Listening for traffic (1.5s)...")

        # 4. Verification & Logging
        start_wait = time.time()
        seen_ids = set()

        while time.time() - start_wait < 1.5:
            incoming = p.can_recv()
            for addr, _, bus in incoming:
                if bus == TARGET_BUS:
                    # Log every unique ID we see
                    seen_ids.add(addr)
                    # Update global traffic timer
                    last_traffic_time = time.time()

        # Filter out our own TX IDs to see if *others* replied
        rx_ids = [hex(x) for x in seen_ids if x not in [ID_POKE, ID_RING]]

        if len(rx_ids) > 0:
            print(f" [V] SUCCESS: Traffic detected on attempt {attempt}.")
            print(f"     Ids Seen: {', '.join(rx_ids)}")
            bus_awake = True
            return True
        else:
            print(" [X] SILENCE: No response.")

    print(" [!] FAILED: Could not wake car after 3 attempts.")
    bus_awake = False
    return False

def connection_thread(p):
    global ac_request, current_temp, fas_counter, transition_active, bus_awake, last_traffic_time

    last_tick = 0

    print("[*] Heartbeat thread active.")

    while keep_running:
        current_time = time.time()

        # --- 1Hz HEARTBEAT (0x5A9 + 0x5A7) ---
        # Only send if we think the bus is awake or we are forcing it
        if current_time - last_tick > 1.0:
            last_tick = current_time

            # Update bus state based on recent traffic (timeout 2s)
            if current_time - last_traffic_time < 2.0:
                bus_awake = True
            else:
                bus_awake = False

            # Keep sending heartbeat if awake to HOLD it awake
            if bus_awake:
                try:
                    p.can_send(ID_HEARTBEAT_1, b'\x00'*8, TARGET_BUS)
                except: pass

                # Manage Transition Logic
                if transition_active:
                    fas_counter += 1
                    if fas_counter >= 10:
                        transition_active = False
                        fas_counter = 0

                b0 = 0x60 if transition_active else 0x00
                msg_5a7 = struct.pack("BBBBBBBB", b0, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
                try:
                    p.can_send(ID_HEARTBEAT_2, msg_5a7, TARGET_BUS)
                except: pass

        # --- COMMAND HANDLING ---
        if ac_request is not None:
            if not bus_awake:
                print(" [!] WARNING: Sending command while bus is sleeping...")

            transition_active = True
            fas_counter = 0

            if ac_request == "START":
                target = current_temp if current_temp > 15 else 21.0
                print(f" -> TX: AC START (Target {target:.1f}°C)")
                cmd = get_ac_command(enable=True, temp_c=target)
            else:
                print(f" -> TX: AC STOP")
                cmd = get_ac_command(enable=False, temp_c=21.0)

            for _ in range(5):
                p.can_send(ID_AC_CMD, cmd, TARGET_BUS)
                time.sleep(0.1)
            ac_request = None

        # --- READ TEMP & TRAFFIC MONITOR ---
        incoming = p.can_recv()
        for addr, dat, bus in incoming:
            if bus == TARGET_BUS:
                last_traffic_time = current_time # Update watchdog

                if addr == ID_KNOB_READ and len(dat) >= 5:
                    val = dat[4] / 2.0
                    if val > 0: current_temp = val

        time.sleep(0.01)

def main():
    global keep_running, ac_request, bus_awake

    try:
        print("[*] Initializing Panda...")
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
    except Exception as e:
        print(f"[!] Error: {e}")
        return

    # 1. Initial Wake Up
    perform_wakeup_handshake(p)

    # 2. Start Background Thread
    t = threading.Thread(target=connection_thread, args=(p,))
    t.start()

    print("\n--- CONTROL INTERFACE ---")
    print(" 1 : Turn AC ON")
    print(" 0 : Turn AC OFF")
    print(" w : Retry Wake-Up Handshake")
    print(" q : Quit")
    print("-------------------------")

    try:
        while True:
            # Live Status Line
            status = "ACTIVE" if bus_awake else "SLEEP "
            print(f"\r [Bus: {status}] Knob Temp: {current_temp:.1f}°C ", end="")

            user_input = input().strip().lower()

            if user_input == '1':
                ac_request = "START"
            elif user_input == '0':
                ac_request = "STOP"
            elif user_input == 'w':
                # Manual Trigger
                perform_wakeup_handshake(p)
            elif user_input == 'q':
                break
    except KeyboardInterrupt:
        pass
    finally:
        keep_running = False
        t.join()
        p.set_safety_mode(Panda.SAFETY_SILENT)
        print("\n[*] Exited.")

if __name__ == "__main__":
    main()