import time
import struct
import threading
from panda import Panda

# --- CONFIGURATION ---
TARGET_BUS = 1         # 0=CAN1, 1=CAN2
BUS_SPEED  = 100       # 100kbps

# --- IDs ---
ID_POKE       = 0x69E
ID_RING       = 0x43D
ID_CMD_RW     = 0x69E  # Request ID

# --- GLOBAL ---
keep_running = True
p = None

def perform_wakeup_handshake(p):
    print("[-] Waking Bus...")
    try:
        p.can_send(ID_POKE, b'\x14\x51', TARGET_BUS)
        time.sleep(0.02)
        p.can_send(ID_RING, struct.pack("8B", 0x1D, 0x02, 0x02, 0x00, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        time.sleep(0.06)
        p.can_send(ID_RING, struct.pack("8B", 0x00, 0x01, 0x02, 0x04, 0x00, 0x14, 0x00, 0x00), TARGET_BUS)
        print("[-] Wake Handshake Sent.")
        return True
    except: return False

def send_heartbeat(p):
    """Minimal Heartbeat to keep bus alive during test"""
    while keep_running:
        try:
            # 0x5A9 (Keep Alive)
            p.can_send(0x5A9, b'\x00'*8, TARGET_BUS)
            # 0x5A7 (Status)
            p.can_send(0x5A7, b'\x00\x16\x00\x00\x00\x00\x00\x00', TARGET_BUS)
        except: pass
        time.sleep(1.0)

def main():
    global keep_running, p
    try:
        print("[*] Initializing Panda...")
        p = Panda()
        p.set_can_speed_kbps(TARGET_BUS, BUS_SPEED)
        p.set_safety_mode(Panda.SAFETY_ALLOUTPUT)

        # 1. Wake Up
        perform_wakeup_handshake(p)

        # Start Heartbeat in background
        t = threading.Thread(target=send_heartbeat, args=(p,))
        t.start()

        # Give it a second to settle
        time.sleep(1.0)

        print("\n[*] SENDING PROFILE REQUEST NOW...")
        print("[*] Watching bus for 3 seconds (Raw Dump)...")
        print("-" * 60)
        print(f"{'TIME':<10} {'ID':<8} {'DATA'}")
        print("-" * 60)

        # 2. Send Request: 90 04 19 59 27 00 00 01
        req = struct.pack("BBBBBBBB", 0x90, 0x04, 0x19, 0x59, 0x27, 0x00, 0x00, 0x01)
        p.can_send(ID_CMD_RW, req, TARGET_BUS)

        # 3. Sniff Loop
        start_time = time.time()
        req_sent_time = start_time

        while time.time() - start_time < 3.0:
            incoming = p.can_recv()
            for addr, dat, bus in incoming:
                if bus == TARGET_BUS:
                    # Ignore our own Heartbeats to clear clutter
                    if addr in [0x5A9, 0x5A7]: continue

                    # Highlight our Request
                    if addr == ID_CMD_RW and dat == req:
                        print(f"{0.000:<10.3f} {hex(addr):<8} {dat.hex()} <--- MY REQUEST")
                    else:
                        dt = time.time() - req_sent_time
                        print(f"{dt:<10.3f} {hex(addr):<8} {dat.hex()}")

            time.sleep(0.001)

        print("-" * 60)
        print("[*] Capture Complete.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        keep_running = False
        if p: p.set_safety_mode(Panda.SAFETY_SILENT)

if __name__ == "__main__":
    main()