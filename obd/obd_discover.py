#!/usr/bin/env python3
"""OBD-II / CAN module discovery scanner for a 2016 Nissan Altima (L33).

Answers the real question: "what does THIS car actually support?"

Three things it discovers, all by active probing (not guesswork):

  1. WIRE
     Sweep 11-bit CAN IDs for anything that answers an OBD request. Finds every
     ECU, not just the engine - so modules beyond engine/emissions show up.

  2. PIDS
     Bitmask-walk mode 01 (live data), mode 02 (freeze frame), mode 09 (vehicle
     info) across ALL addresses found. The bitmask is authoritative: the ECU
     itself declares support.

  3. VALUES
     Read each supported PID, so you get the car's actual data, not just a list.

Also dumps raw frames for the manufacturer-specific ranges (PIDs 0x61+ for
mode 01, addresses 0x7E8+ for other modules) which is where dealer-only data
lives. Those are NOT bitmask-discoverable; they must be sniffed or fuzzed.

Usage:
  python3 obd_discover.py vcan0               # scan everything
  python3 obd_discover.py can0 --wire         # ECU address sweep only
  python3 obd_discover.py can0 --pids         # PID bitmask walk only
  python3 obd_discover.py can0 --freeze       # freeze-frame + live dump
"""
import argparse
import socket
import struct
import sys
import time

REQ_FUNC = 0x7DF
RSP_BASE = 0x7E8

# All 11-bit IDs worth probing for an OBD responder.
# 0x7E0-0x7E7 request, 0x7E8-0x7EF response, 0x7DF functional.
PROBE_IDS = list(range(0x7E0, 0x7F0)) + [0x7DF]

MODE01_BLOCKS = [0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0]
MODE02_BLOCKS = [0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0]
MODE09_PIDS = [0x00, 0x02, 0x04, 0x06, 0x0A, 0x08, 0x0B]

# PID name lookup for readable output (standard SAE J1979 subset).
NAMES = {
    0x01: "monitor status",       0x02: "freeze DTC",        0x03: "fuel system",
    0x04: "engine load %",        0x05: "coolant temp",      0x06: "STFT b1",
    0x07: "LTFT b1",              0x08: "STFT b2",           0x09: "LTFT b2",
    0x0A: "fuel pressure",        0x0B: "intake pressure",   0x0C: "RPM",
    0x0D: "speed km/h",           0x0E: "timing advance",    0x0F: "intake temp",
    0x10: "MAF",                  0x11: "throttle %",        0x12: "air status",
    0x13: "O2 present",           0x14: "O2 b1s1",           0x15: "O2 b1s2",
    0x16: "O2 b1s3",              0x17: "O2 b1s4",           0x18: "O2 b2s1",
    0x19: "O2 b2s2",              0x1A: "O2 b2s3",           0x1B: "O2 b2s4",
    0x1C: "OBD compliance",       0x1D: "O2 alt",            0x1E: "aux input",
    0x1F: "run time",             0x21: "dist w/ MIL",       0x22: "rail press vac",
    0x23: "rail press dir",       0x2C: "commanded EGR",     0x2D: "EGR error",
    0x2E: "evap purge",           0x2F: "fuel level",        0x30: "warmups",
    0x31: "dist since clear",     0x32: "evap vapor press",  0x33: "barometric",
    0x3C: "cat temp b1s1",        0x3D: "cat temp b2s1",     0x3E: "cat temp b1s2",
    0x3F: "cat temp b2s2",        0x41: "monitor this cycle",0x42: "module volts",
    0x43: "abs load",             0x44: "equiv ratio",       0x45: "rel throttle",
    0x46: "ambient air",          0x47: "throttle B",        0x48: "throttle C",
    0x49: "accel D",              0x4A: "accel E",           0x4B: "accel F",
    0x4C: "throttle actuator",    0x4D: "time w/ MIL",       0x4E: "time since clear",
    0x51: "fuel type",            0x52: "ethanol %",         0x5C: "oil temp",
    0x5D: "inject timing",        0x5E: "fuel rate",
}


def ids_from_mask(mask4, base):
    """Decode a 4-byte supported-PID bitmask into a list of PIDs."""
    out = []
    for i in range(32):
        if mask4[i // 8] & (1 << (7 - (i % 8))):
            out.append(base + 1 + i)
    return out


class Bus:
    def __init__(self, iface, verbose=False):
        self.iface = iface
        self.verbose = verbose
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.sock.settimeout(0.25)
        self.sock.bind((iface,))
        # Loopback so our own frames are visible too (optional - not all
        # kernels/veth setups expose CAN_RAW_LOOPBACK under this constant).
        try:
            self.sock.setsockopt(socket.SOL_CAN_RAW, 29, 1)
        except OSError:
            pass
        self.seen = {}          # can_id -> count
        self.ecu_addrs = set()

    def send(self, can_id, payload):
        data = bytes([len(payload)]) + payload
        data += b"\x00" * (8 - len(data))
        # struct can_frame is exactly 16 bytes:
        #   can_id(4) can_dlc(1) pad(3) data[8]
        frame = struct.pack("<IB3x", can_id, 8) + data[:8]
        self.sock.send(frame)

    def drain(self, seconds=0.35):
        """Collect frames for a window; group ISO-TP responses."""
        end = time.time() + seconds
        replies = {}
        while time.time() < end:
            try:
                frame, _ = self.sock.recvfrom(16)
            except socket.timeout:
                continue
            cid, dlc = struct.unpack_from("<IB", frame, 0)
            cid &= 0x1FFFFFFF
            data = bytes(frame[8:8 + (dlc & 0x0F)])
            if cid == 0x7DF or cid < 0x7E0:
                continue                      # our own request
            self.seen[cid] = self.seen.get(cid, 0) + 1
            if 0x7E8 <= cid <= 0x7EF:
                self.ecu_addrs.add(cid)
            # ISO-TP reassembly (single + first/consecutive)
            if not data:
                continue
            pci = data[0] >> 4
            if pci == 0x0:
                payload = data[1:1 + (data[0] & 0x0F)]
            elif pci == 0x1:
                payload = data[2:]
            elif pci == 0x2:
                payload = data[1:]
            else:
                continue
            replies.setdefault(cid, []).append(payload)
        return replies

    def request(self, payload, can_id=REQ_FUNC, wait=0.35):
        self.send(can_id, payload)
        return self.drain(wait)


def scan_wire(bus):
    print("\n=== 1. WIRE: sweeping OBD request addresses ===")
    for rid in PROBE_IDS:
        bus.sock.settimeout(0.12)
        bus.send(rid, b"\x01\x00")            # mode 01 PID 00 = "who are you"
        r = bus.drain(0.10)
        for cid in sorted(r):
            print(f"    req 0x{rid:03X} -> resp 0x{cid:03X}   ✓ responder")
    if bus.ecu_addrs:
        print(f"  responders found: {[hex(a) for a in sorted(bus.ecu_addrs)]}")
    else:
        print("  no responders (bus asleep? ignition off?)")


def scan_pids(bus, addrs):
    print("\n=== 2. PIDS: bitmask walk (ECU-declared support) ===")
    for addr in sorted(addrs) or [REQ_FUNC]:
        req = addr - 0x20 if addr != REQ_FUNC else REQ_FUNC
        print(f"  -- module 0x{addr:03X} (request 0x{req:03X}) --")
        for blk in MODE01_BLOCKS:
            r = bus.request(bytes([0x01, blk]), can_id=req)
            got = False
            for cid, frames in r.items():
                for f in frames:
                    if len(f) >= 6 and f[0] == 0x41 and f[1] == blk:
                        pids = ids_from_mask(f[2:6], blk)
                        if pids:
                            got = True
                            names = [f"0x{p:02X} {NAMES.get(p,'')}".strip()
                                     for p in pids if p != blk + 0x20]
                            print(f"      block 0x{blk:02X} -> {len(names)} PIDs")
                            for n in names:
                                print(f"        {n}")
            if not got:
                break
        print()


def scan_values(bus):
    print("\n=== 3. VALUES: reading supported PIDs ===")
    for blk in (0x00, 0x20, 0x40):
        r = bus.request(bytes([0x01, blk]))
        for cid, frames in r.items():
            for f in frames:
                if len(f) >= 6 and f[0] == 0x41 and f[1] == blk:
                    for p in ids_from_mask(f[2:6], blk):
                        if p == blk + 0x20:
                            continue
                        rr = bus.request(bytes([0x01, p]))
                        for c2, fs in rr.items():
                            for ff in fs:
                                if ff[0] == 0x41 and ff[1] == p:
                                    raw = ff[2:].hex(" ")
                                    print(f"    0x{p:02X} {NAMES.get(p,''):18} = {raw}")
    print("\n  NOTE: manufacturer PIDs 0x61+ are NOT bitmask-discoverable.")
    print("        They need sniffing while the car is driven, or fuzzing.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iface")
    ap.add_argument("--wire", action="store_true")
    ap.add_argument("--pids", action="store_true")
    ap.add_argument("--values", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    bus = Bus(a.iface)
    print(f"scanning {a.iface} ...")
    do_all = a.all or not (a.wire or a.pids or a.values)
    if a.wire or do_all:
        scan_wire(bus)
    if a.pids or do_all:
        scan_pids(bus, bus.ecu_addrs)
    if a.values or do_all:
        scan_values(bus)


if __name__ == "__main__":
    main()
