#!/usr/bin/env python3
"""Robust ATMA line parser, validated against real Altima capture data.

Three real-world quirks the naive regex missed (found by testing against the
39-line capture from this vehicle):

  1. PROMPT GLUE      ">2DE 00 00 ..."  - the ELM327 prompt '>' can land at the
                      start of a data line. Strip any leading prompt chars.
  2. NO DLC FIELD     With AT H1 and no AT D1, lines are "ID D0..D7" - there is
                      NO length byte. A regex with an optional digit group will
                      silently eat the first data byte.
  3. BUFFER FULL      On output-buffer overflow the adapter aborts mid-frame, so
                      a line can contain a partial frame, or two frames run
                      together: "176 00 00 00 003 03 FD 00 37 10".
                      These must be handled, not dropped silently.
"""
import re
import sys

HEX2 = re.compile(r"^[0-9A-Fa-f]{2}$")
HEX_ID = re.compile(r"^[0-9A-Fa-f]{1,8}$")


def parse_line(line):
    """Return (can_id, data_bytes) or None if unusable.

    Handles: leading prompt, with/without DLC, merged frames, short frames.
    """
    if not line:
        return None
    # 1. strip prompt / junk prefixes
    s = line.strip().lstrip(">").strip()
    # strip the trailing prompt if present
    s = s.rstrip(">").strip()
    if not s:
        return None
    toks = s.split()
    if len(toks) < 2:
        return None

    # 2. first token is the CAN ID
    if not HEX_ID.match(toks[0]):
        return None
    try:
        can_id = int(toks[0], 16)
    except ValueError:
        return None

    rest = toks[1:]

    # 3. optional DLC: a single hex digit token before 8 two-char byte tokens.
    #    Only treat it as DLC if the remaining tokens are all valid bytes.
    if len(rest) >= 9 and len(rest[0]) == 1 and all(HEX2.match(t) for t in rest[1:]):
        rest = rest[1:]

    # 4. keep only valid 2-hex-char byte tokens (drops merged/garbage fragments)
    data = [t for t in rest if HEX2.match(t)]

    if not data:
        return None

    # 5. a valid CAN frame is 8 bytes; longer means frames ran together
    #    (BUFFER FULL). Truncate rather than discard - the first 8 bytes are a
    #    real frame.
    data = data[:8]
    # short frames are genuinely incomplete; still emit, the app layer decides
    return can_id, bytes(int(t, 16) for t in data)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/altima_atma_capture.txt"
    lines = open(path, errors="replace").read().splitlines()
    ok = fail = 0
    for l in lines:
        if not l.strip():
            continue
        r = parse_line(l)
        if r is None:
            fail += 1
            print(f"  SKIP  {l[:60]!r}")
        else:
            ok += 1
    print(f"\n  parsed OK : {ok}")
    print(f"  skipped   : {fail}")
    print(f"  total     : {ok + fail}")


if __name__ == "__main__":
    main()
