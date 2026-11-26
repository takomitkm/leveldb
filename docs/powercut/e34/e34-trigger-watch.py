#!/usr/bin/env python3
"""Watch a fixed byte offset in the E34LOG volume image for the E34CUT
trigger, then print the monotonic timestamps the host needs to kill QEMU.

The guest writes the marker at a pre-allocated, fixed offset (no truncate),
so polling one spot in the raw image is enough.  Host-side monotonic times are
recorded around the detection so the API-return-to-kill latency is measured,
never assumed to be zero.

Usage: python e34-trigger-watch.py <image> <offset> <timeout-seconds>
Prints: DETECTED <mono_ns> <token>   (then exits 0), or TIMEOUT (exit 4).
"""
import sys, time
path, offset, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
mode = sys.argv[4] if len(sys.argv) > 4 else "token"
deadline = time.monotonic() + timeout
first = None
# unbuffered: the guest writes this file while we poll it; a
# buffered reader would keep returning the cached placeholder.
with open(path, "rb", buffering=0) as fh:
    while time.monotonic() < deadline:
        fh.seek(offset)
        chunk = fh.read(51)
        if mode == "change":
            if first is None:
                first = chunk
            elif chunk != first:
                print("DETECTED %d %s" % (time.monotonic_ns(),
                      chunk.decode("ascii", "replace").strip()))
                sys.exit(0)
        elif chunk.startswith(b"E34CUT,"):
            print("DETECTED %d %s" % (time.monotonic_ns(),
                  chunk.decode("ascii", "replace").strip()))
            sys.exit(0)
        time.sleep(0.05)
print("TIMEOUT")
sys.exit(4)
