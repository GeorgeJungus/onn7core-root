#!/usr/bin/env python3
"""Read-only DTC + Mode 06 monitor dump for the 2016 Altima (L33).

STRICTLY READ-ONLY. Modes 01/03/06/07/09/0A only. Mode 04 (clear) is NEVER sent.

Why a dedicated script: the previous single-shot read returned '4A01059F' with
no headers, which is ambiguous - the trailing 9F could be a second DTC's first
byte, padding, or a framing artifact. This run uses ATH1 (headers ON) so every
response carries its ECU address, and ATD1 (DLC on), then reads each ECU
individually via ATSH so there is no functional-broadcast interleaving.

Also walks mode 06 properly: MID list first, then every supported MID, because
0600 only advertises which MIDs exist - the actual test results live per-MID.

Usage: python3 obd_dtc_mode06.py
"""
import asyncio
import re
from bleak import BleakScanner, BleakClient

MAC = "AA:BB:CC:DD:EE:FF"
SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX_CH = "0000fff1-0000-1000-8000-00805f9b34fb"
TX_CH = "0000fff2-0000-1000-8000-00805f9b34fb"

# Mode 06 MID names (SAE J1979 / ISO 15031-6 subset)
MIDS = {
    0x01: "O2 sensor monitor b1s1", 0x02: "O2 sensor b1s2",
    0x21: "Catalyst monitor b1",    0x31: "EGR monitor b1",
    0x35: "VVT monitor b1",         0x39: "EVAP 0.150in",
    0x3A: "EVAP 0.090in",           0x3B: "EVAP 0.040in",
    0x3C: "EVAP 0.020in",           0x3D: "Purge flow monitor",
    0x41: "O2 heater b1s1",         0xA1: "Misfire general",
    0xA2: "Misfire cyl 1",          0xA3: "Misfire cyl 2",
    0xA4: "Misfire cyl 3",          0xA5: "Misfire cyl 4",
}


def dtc(b1, b2):
    cat = ["P", "C", "B", "U"][(b1 & 0xC0) >> 6]
    return f"{cat}{(b1 >> 4) & 0x03}{b1 & 0x0F}{(b2 >> 4) & 0x0F}{b2 & 0x0F}"


class Elm:
    def __init__(self, c, tx):
        self.c, self.tx = c, tx
        self.buf = bytearray()

    def feed(self, d): self.buf.extend(d)

    async def cmd(self, s, wait=1.4):
        self.buf.clear()
        await self.c.write_gatt_char(self.tx, (s + "\r").encode(), response=False)
        await asyncio.sleep(wait)
        t = bytes(self.buf).decode(errors="replace").replace("\r", "\n")
        return [l.strip() for l in t.split("\n")
                if l.strip() and l.strip() != ">" and not l.strip().startswith("SEARCHING")]


async def main():
    dev = await BleakScanner.find_device_by_address(MAC, timeout=15)
    if not dev:
        print("VEEPEAK not found"); return

    async with BleakClient(dev) as c:
        svc = c.services.get_service(SVC)
        rx = svc.get_characteristic(RX_CH)
        tx = svc.get_characteristic(TX_CH)
        e = Elm(c, tx)
        await c.start_notify(rx, lambda _c, d: e.feed(d))

        print("=== setup (headers ON so responses are attributable) ===")
        for cmd, w in (("ATZ", 2.5), ("ATE0", 0.8), ("ATL0", 0.8),
                       ("ATS1", 0.8), ("ATH1", 0.8), ("ATD1", 0.8),
                       ("ATSP6", 1.5), ("ATCAF1", 1.0)):
            r = await e.cmd(cmd, w)
            print(f"  {cmd:8} -> {r}")

        print("\n=== DTCs per ECU (physical addressing, no broadcast race) ===")
        for label, req in (("ECM  (0x7E0)", "7E0"), ("TCM  (0x7E1)", "7E1")):
            print(f"\n  -- {label} --")
            r = await e.cmd(f"ATSH{req}", 1.0)
            for mode, mname in (("03", "stored"), ("07", "pending"), ("0A", "permanent")):
                r = await e.cmd(mode, 2.5)
                # with headers: '7E8 06 43 01 05 9F ...'  or '7E8 02 43 00'
                parsed = []
                for line in r:
                    hx = re.findall(r"[0-9A-Fa-f]{2}", line)
                    if "43" in hx or "47" in hx or "4A" in hx:
                        parsed.append(line)
                print(f"    mode {mode} ({mname:9}) -> {r}")
                for line in parsed:
                    hx = re.findall(r"[0-9A-Fa-f]{2}", line)
                    # find the response marker
                    for i, b in enumerate(hx):
                        if b in ("43", "47", "4A"):
                            body = hx[i + 1:]
                            if body and body[0] != "00":
                                ncount = int(body[0], 16)
                                pairs = body[1:1 + ncount * 2]
                                codes = [dtc(int(pairs[j], 16), int(pairs[j + 1], 16))
                                         for j in range(0, len(pairs) - 1, 2)]
                                print(f"        DECODED: {ncount} code(s): {codes}")
                            break

        print("\n=== MODE 06: on-board monitor test results ===")
        r = await e.cmd("ATSH7E0", 1.0)
        r = await e.cmd("0600", 2.5)
        print(f"  0600 (supported MID bitmask) -> {r}")

        mids = []
        for line in r:
            hx = re.findall(r"[0-9A-Fa-f]{2}", line)
            for i, b in enumerate(hx):
                if b == "46":
                    body = hx[i + 1:]
                    if len(body) >= 5 and body[0] == "00":
                        mask = "".join(body[1:5])
                        bb = bytes.fromhex(mask)
                        for k in range(32):
                            if bb[k // 8] & (1 << (7 - (k % 8))):
                                mids.append(k + 1)
                    break
        print(f"  supported MIDs: {[hex(m) for m in mids]}")

        print("\n  -- per-MID test results (ECM) --")
        for mid in mids:
            if mid in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0):
                continue
            r = await e.cmd(f"06{mid:02X}", 2.0)
            tag = MIDS.get(mid, "")
            print(f"    MID 0x{mid:02X} {tag:26} -> {r}")

        await c.stop_notify(rx)


asyncio.run(main())
