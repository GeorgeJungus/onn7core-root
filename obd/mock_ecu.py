#!/usr/bin/env python3
"""Mock OBD-II ECU on a SocketCAN interface - validation harness.

Purpose: prove the PID-discovery pipeline works BEFORE it touches a real car.
It answers the same way a 2016 Nissan Altima (L33) engine ECU would:
ISO 15765-4, 11-bit CAN, 500 kbit/s, request 0x7DF/0x7E0 -> response 0x7E8.

Simulated support (deliberately a realistic subset, not "everything"):
  mode 01: standard live PIDs, bitmask-discoverable
  mode 03: stored DTCs
  mode 09: VIN (multi-frame ISO-TP, so framing gets exercised)

Run:  python3 mock_ecu.py vcan0
"""
import socket
import struct
import sys
import time

REQ_FUNC = 0x7DF      # functional broadcast (all ECUs answer)
REQ_ECU = 0x7E0       # physical request to engine ECU
RSP_ECU = 0x7E8       # engine ECU response

VIN = b"1N4AL3AP0GC123456"   # 17 bytes, fabricated

# Supported PID bitmask for 0100 (PIDs 01-20). Bit 0 = PID 0x01.
# Nissan L33 typically supports: 01,03,04,05,06,07,0C,0D,0E,0F,10,11,13,14,
# 15,1C,1F,20,21,2F,30,31,33,3C,3D,3E,3F,40,41,42,43,45,46,47,49,4A,4C,4D,
# 4E,50,51,52,5C  (approximation of a real 2016 QR25DE/VQ35DE set)
PIDS_01_20 = [0x01,0x03,0x04,0x05,0x06,0x07,0x0C,0x0D,0x0E,0x0F,0x10,0x11,
              0x13,0x14,0x15,0x1C,0x1F,0x20]
PIDS_21_40 = [0x21,0x2F,0x30,0x31,0x33,0x3C,0x3D,0x3E,0x3F,0x40]
PIDS_41_60 = [0x41,0x42,0x43,0x45,0x46,0x47,0x49,0x4A,0x4C,0x4D,0x4E,0x50,
              0x51,0x52,0x5C]

# Live values, keyed by PID -> payload bytes
LIVE = {
    0x04: bytes([0x40]),                    # engine load 50%
    0x05: bytes([0x7B]),                    # coolant 83 C
    0x06: bytes([0x80,0x80]),               # STFT b1
    0x07: bytes([0x80,0x80]),               # LTFT b1
    0x0C: bytes([0x1A,0xF8]),               # RPM = 1726
    0x0D: bytes([0x00]),                    # speed 0
    0x0E: bytes([0x80]),                    # timing advance
    0x0F: bytes([0x3C]),                    # intake temp 20 C
    0x10: bytes([0x02,0x80]),               # MAF
    0x11: bytes([0x40]),                    # throttle 25%
    0x13: bytes([0x03]),                    # O2 sensors present
    0x14: bytes([0x80,0xFF]),               # O2 B1S1
    0x15: bytes([0x80,0x80]),               # O2 B1S2
    0x1C: bytes([0x06]),                    # OBD compliance: EOBD
    0x1F: bytes([0x0E,0x10]),               # run time
    0x2F: bytes([0x40]),                    # fuel level 25%
    0x42: bytes([0x37,0x9C]),               # control module voltage 14.2 V
    0x45: bytes([0x20]),                    # relative throttle
    0x46: bytes([0x34]),                    # ambient air 32 C
    0x5C: bytes([0x5A]),                    # oil temp 50 C
}
DTCS = []           # no stored codes; set e.g. [b"\x01\x43"] for P0143


def bitmask(pids):
    """4-byte supported-PID bitmask. Highest PID supported gets bit set."""
    b = bytearray(4)
    for p in pids:
        idx = p - 1
        if 0 <= idx < 32:
            b[idx // 8] |= 1 << (7 - (idx % 8))
    # The next-range marker bit (the final PID of each block, 0x20/0x40/0x60)
    # signals "more PIDs exist beyond this block".
    return bytes(b)


def isotp_single(payload):
    """Frame a payload <=7 bytes as an ISO-TP single frame."""
    return bytes([len(payload)]) + payload + b"\x00" * (7 - len(payload))


def isotp_multi(payload):
    """Frames for payload >7 bytes: first frame + consecutive frames."""
    n = len(payload)
    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
    rest = payload[6:]
    seq = 1
    while rest:
        chunk = rest[:7]
        frames.append(bytes([0x20 | seq]) + chunk + b"\x00" * (7 - len(chunk)))
        rest = rest[7:]
        seq += 1
    return frames


def handle(req):
    """Return a list of response payloads (pre-ISO-TP) for one request."""
    if len(req) < 1:
        return []
    mode = req[0]

    if mode == 0x01:                                  # live data
        if len(req) < 2:
            return []
        pid = req[1]
        if pid == 0x00: return [bytes([0x41, 0x00]) + bitmask(PIDS_01_20)]
        if pid == 0x20: return [bytes([0x41, 0x20]) + bitmask(PIDS_21_40)]
        if pid == 0x40: return [bytes([0x41, 0x40]) + bitmask(PIDS_41_60)]
        if pid in LIVE: return [bytes([0x41, pid]) + LIVE[pid]]
        return []                                     # unsupported -> no reply

    if mode == 0x03:                                  # stored DTCs
        body = b"".join(DTCS)
        return [bytes([0x43, len(DTCS)]) + body] if body else [bytes([0x43, 0x00])]

    if mode == 0x04:                                  # clear DTCs
        return [bytes([0x44])]

    if mode == 0x09:                                  # vehicle info
        if len(req) < 2:
            return []
        pid = req[1]
        if pid == 0x00: return [bytes([0x49, 0x00, 0x55, 0x40, 0x00, 0x00])]
        if pid == 0x02: return [bytes([0x49, 0x02, 0x01]) + VIN]
        return []

    if mode == 0x02:                                  # freeze frame
        return []

    return []                                         # unknown mode: stay silent


def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else "vcan0"
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    print(f"mock ECU up on {iface}  (req 0x7DF/0x7E0 -> rsp 0x7E8)", flush=True)

    while True:
        try:
            frame, _ = s.recvfrom(16)
        except KeyboardInterrupt:
            break
        # Parse the CAN frame with unpack_from - avoids the int/bytes ambiguity
        # when slicing the struct-formatted sockaddr_can header.
        can_id, can_dlc = struct.unpack_from("<IB", frame, 0)
        can_id &= 0x1FFFFFFF
        data = bytes(frame[8:8 + (can_dlc & 0x0F)])

        # Only respond to functional broadcast or our physical address.
        if can_id not in (REQ_FUNC, REQ_ECU):
            continue

        payload = data[1:1 + (data[0] & 0x0F)]
        replies = handle(payload)
        for r in replies:
            # Both paths must yield a LIST of frames. `frames(x)` returns a bare
            # bytes object, and iterating bytes yields ints - which then breaks
            # len(). Normalise to a list here.
            frames = isotp_single(r) if len(r) <= 7 else isotp_multi(r)
            if isinstance(frames, (bytes, bytearray)):
                frames = [bytes(frames)]
            for fr in frames:
                # struct can_frame is 16 bytes: can_id(4) dlc(1) pad(3) data[8]
                s.send(struct.pack("<IB3x", RSP_ECU, len(fr)) + bytes(fr)[:8])
                time.sleep(0.002)


if __name__ == "__main__":
    main()
