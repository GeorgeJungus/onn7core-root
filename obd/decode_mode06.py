#!/usr/bin/env python3
"""Reassemble ISO-TP multi-frame Mode 06 data and decode DTCs correctly.

Two fixes over the first pass:
  1. DTC nibbles must be HEX. The earlier decoder used str() on a nibble, so
     0x0F printed as "15" - turning P059F into the non-existent "P05915".
  2. Mode 06 responses are ISO-TP multi-frame (0x10 0x5B = 91 bytes). The first
     pass printed raw frames instead of reassembling, so the test data was
     unreadable.

Reassembles from a saved capture - no bus traffic at all in this script.
"""
import re
import sys

CAP = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dtc06_full.txt"

# ---------------------------------------------------------------- DTC decode
def dtc(b1, b2):
    """SAE J2012 DTC from two bytes. Nibbles formatted as HEX."""
    cat = ["P", "C", "B", "U"][(b1 & 0xC0) >> 6]
    return f"{cat}{(b1 >> 4) & 0x03}{b1 & 0x0F:X}{(b2 >> 4) & 0x0F:X}{b2 & 0x0F:X}"


# ------------------------------------------------------------- ISO-TP reassembly
def reassemble(frames):
    """frames: list of bytes (the 8-byte CAN payloads, after the '7E8 8' prefix)."""
    out = bytearray()
    for f in frames:
        if not f:
            continue
        pci = f[0] >> 4
        if pci == 0x0:                      # single frame
            n = f[0] & 0x0F
            out.extend(f[1:1 + n])
        elif pci == 0x1:                    # first frame
            out.extend(f[2:])
        elif pci == 0x2:                    # consecutive frame
            out.extend(f[1:])
    return bytes(out)


def parse_frames(text):
    """Pull every '7E8 8 AA BB ...' frame payload, in order.

    Uses finditer, not search: a single search only returns the FIRST frame of
    an ISO-TP multi-frame response, which silently truncated the reassembly to
    6 bytes and hid the whole Mode 06 test record.
    """
    frames = []
    for m in re.finditer(r"7E8\s+8\s+((?:[0-9A-Fa-f]{2}\s+){1,7}[0-9A-Fa-f]{2})", text):
        frames.append([int(x, 16) for x in m.group(1).split()])
    return frames


def main():
    text = open(CAP, errors="replace").read()

    print("=" * 66)
    print("DTC DECODE (hex nibbles - corrects the earlier P05915/P059F error)")
    print("=" * 66)
    for line in text.split("\n"):
        if "4A" in line and "7E8" in line:
            hx = re.findall(r"[0-9A-Fa-f]{2}", line)
            i = hx.index("4A")
            body = hx[i + 1:]
            cnt = int(body[0], 16)
            codes = [dtc(int(body[1 + 2 * k], 16), int(body[2 + 2 * k], 16))
                     for k in range(cnt)]
            print(f"  raw: {' '.join(hx)}")
            print(f"  permanent DTCs ({cnt}): {codes}")
    print("\n  TCM (0x7E9) responses '7F <mode> 11' = serviceNotSupported:")
    print("    the TCM does not implement modes 03/07/0A - ECM only.")

    print()
    print("=" * 66)
    print("MODE 06 - ISO-TP REASSEMBLED TEST RESULTS")
    print("=" * 66)

    # isolate each MID block
    for mid_label, mid in (("MID 0x01 (O2 sensor b1s1)", 0x01),
                           ("MID 0x02 (O2 sensor b1s2)", 0x02)):
        # grab the lines belonging to this MID (everything to end of that block)
        seg = text.split(f"MID 0x{mid:02X}")[1]
        seg = seg.split("MID 0x")[0] if "MID 0x" in seg[1:] else seg
        frames = parse_frames(seg)
        payload = reassemble(frames)
        print(f"\n  {mid_label}:  {len(payload)} bytes reassembled")
        print(f"    raw: {payload.hex(' ')}")

        # Mode 06 layout: 46 MID, then repeating (TID, UAS, val_hi, val_lo, min_hi, min_lo, max_hi, max_lo)
        if not payload or payload[0] != 0x46:
            print("    (unexpected prefix)")
            continue
        midv = payload[1]
        body = payload[2:]
        print(f"    46 {midv:02X}  -> TID records:")
        off = 0
        while off + 8 <= len(body):
            tid, uas, vh, vl, nh, nl, xh, xl = body[off:off + 8]
            val = (vh << 8) | vl
            mn = (nh << 8) | nl
            mx = (xh << 8) | xl
            print(f"      TID 0x{tid:02X}  UAS 0x{uas:02X}   "
                  f"value={val:5}  min={mn:5}  max={mx:5}")
            off += 8


main()
