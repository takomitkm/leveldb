#!/usr/bin/env python3
"""E34.1/E34.2 host-side trigger watcher.

Polls one fixed byte offset in the E34LOG volume image for the guest's
`E34CUT` token, then kills the QEMU process and prints monotonic timestamps
around both events.

Why not PowerShell: `Stop-Process` costs ~2.3 s of cold start on this host,
which would dominate the latency this experiment measures.  Two paths are
tried in order:

1. kernel32 `OpenProcess` + `TerminateProcess` via ctypes.  On this rig the
   MSYS Python gets a NULL handle even for a live pid, so this is best-effort.
2. `taskkill.exe -F -PID <pid>` (~48 ms measured).  Note the dash form: MSYS
   rewrites `/F` into a path.

Usage:
  python e34-trigger-watch.py <image> <offset> <timeout> [token|change]
      [--kill-pid N] [--out receipt.json]

Prints one JSON line with detected_mono_ns, kill_req_mono_ns and
kill_done_mono_ns. Exit 0 on trigger, 4 on timeout.
"""
import argparse
import json
import subprocess
import sys
import time

PROCESS_TERMINATE = 0x0001

# The rig host's Python is an MSYS (POSIX) build, which has no ctypes.windll;
# kernel32 via CDLL works on both build flavours.


def _kernel32():
    import ctypes as _ct
    if hasattr(_ct, "windll"):
        return _ct.windll.kernel32
    k = _ct.CDLL("kernel32.dll")
    # Without prototypes a CDLL call passes the pid as a 32-bit int and
    # truncates the returned HANDLE -- TerminateProcess then fails.
    k.OpenProcess.argtypes = [_ct.c_uint32, _ct.c_int, _ct.c_uint32]
    k.OpenProcess.restype = _ct.c_void_p
    k.TerminateProcess.argtypes = [_ct.c_void_p, _ct.c_uint32]
    k.TerminateProcess.restype = _ct.c_int
    k.CloseHandle.argtypes = [_ct.c_void_p]
    k.CloseHandle.restype = _ct.c_int
    return k


def kill_pid(pid: int) -> str:
    """Kill pid; return the mechanism that was used."""
    try:
        k32 = _kernel32()
        handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            ok = bool(k32.TerminateProcess(handle, 1))
            k32.CloseHandle(handle)
            if ok:
                return "terminateprocess"
    except Exception:  # noqa: BLE001 - fall through to taskkill
        pass
    subprocess.run(["taskkill.exe", "-F", "-PID", str(pid)],
                   capture_output=True)
    return "taskkill"
    ok = bool(k32.TerminateProcess(handle, 1))
    k32.CloseHandle(handle)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("offset", type=int)
    ap.add_argument("timeout", type=float)
    ap.add_argument("mode", nargs="?", default="token",
                    choices=["token", "change"])
    ap.add_argument("--kill-pid", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    deadline = time.monotonic() + args.timeout
    first = None
    hit = None
    # Unbuffered: the guest writes this file while we poll it; a buffered
    # reader would keep returning the cached placeholder.
    with open(args.image, "rb", buffering=0) as fh:
        while time.monotonic() < deadline:
            fh.seek(args.offset)
            chunk = fh.read(51)
            if args.mode == "change":
                if first is None:
                    first = chunk
                elif chunk != first:
                    hit = chunk
                    break
            elif chunk.startswith(b"E34CUT,"):
                hit = chunk
                break
            time.sleep(0.005)          # 5 ms poll
    if hit is None:
        print(json.dumps({"mode": args.mode, "result": "timeout"}))
        return 4

    detected = time.monotonic_ns()
    kill_req = None
    kill_done = None
    mechanism = None
    if args.kill_pid:
        kill_req = time.monotonic_ns()
        mechanism = kill_pid(args.kill_pid)
        kill_done = time.monotonic_ns()
    rec = {"mode": args.mode, "result": "detected",
           "token": hit.decode("ascii", "replace").strip(),
           "detected_mono_ns": detected,
           "kill_req_mono_ns": kill_req,
           "kill_done_mono_ns": kill_done,
           "kill_mechanism": mechanism}
    line = json.dumps(rec)
    print(line)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
