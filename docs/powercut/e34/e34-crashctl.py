#!/usr/bin/env python3
"""E34 - host-side crash controller.

Listens for the guest probe's external channel, records the trial manifest on
the HOST filesystem (never on the tested volume), aligns the guest clock with
the host clock, and triggers the crash at the configured point.

The controller is deliberately dumb about NTFS: it only knows the event names
the probe emits and when to cut the power.

Usage:
    python e34-crashctl.py --port 45000 --manifest out/trial.jsonl \
        --summary out/trial-summary.json \
        --crash-point flush_end --delay-ms 0 \
        [--kill-pid PID] [--kill-cmd "cmdline"] [--rehearsal]

Exit codes:
    0  the trial completed and the manifest was written
    2  the controller could not start (bad arguments, port in use)
    3  the guest never reached the crash point (timeout)
"""
import argparse
import errno
import json
import os
import socket
import subprocess
import sys
import time

# Guest FILETIME epoch offset (100 ns ticks between 1601-01-01 and 1970-01-01).
FILETIME_EPOCH_DELTA = 116444736000000000


def host_filetime() -> int:
    """Host wall clock as a Windows FILETIME (100 ns since 1601-01-01)."""
    return int((time.time() + 11644473600.0) * 10000000)


def now() -> float:
    return time.time()


class Controller:
    def __init__(self, args):
        self.args = args
        self.events = []
        self.guest_offset = None      # host_filetime - guest_filetime
        self.crash_at = None
        self.killed_at = None
        self.trigger_seen = False

    def log(self, obj):
        obj["host_t"] = host_filetime()
        obj["host_wall"] = round(now(), 6)
        # The first timestamped event fixes the guest-to-host clock offset.
        if obj.get("t") and self.guest_offset is None:
            self.guest_offset = obj["host_t"] - obj["t"]
        self.events.append(obj)
        if self.args.verbose:
            print("ctrl:", json.dumps(obj), flush=True)

    def write_manifest(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.args.manifest)),
                    exist_ok=True)
        with open(self.args.manifest, "w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        summary = {
            "events": len(self.events),
            "guest_clock_offset_100ns": self.guest_offset,
            "crash_point": self.args.crash_point,
            "delay_ms": self.args.delay_ms,
            "crash_triggered": self.trigger_seen,
            "kill_timestamp": self.killed_at,
            "trial_end_to_kill_ms": (
                None if self.killed_at is None or not self.events else
                round(self.killed_at - self.events[-1]["host_wall"], 3)),
            "rehearsal": bool(self.args.rehearsal),
            "channel_overhead_ms": self.measure_overhead(),
        }
        if self.args.summary:
            os.makedirs(os.path.dirname(os.path.abspath(self.args.summary)),
                        exist_ok=True)
            with open(self.args.summary, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=1)
        return summary

    def measure_overhead(self):
        """Round-trip cost of the marker channel, in ms.

        Measured as the difference between the guest's own timestamp and the
        controller's receipt time for the same event (one direction only; the
        reverse direction is not used by the protocol).
        """
        if self.guest_offset is None:
            return None
        deltas = []
        for ev in self.events:
            if ev.get("t") and ev.get("host_t"):
                deltas.append((ev["host_t"] - ev["t"]) / 10000.0)
        if not deltas:
            return None
        deltas.sort()
        return {
            "min_ms": round(deltas[0], 4),
            "median_ms": round(deltas[len(deltas) // 2], 4),
            "max_ms": round(deltas[-1], 4),
            "samples": len(deltas),
        }

    def trigger_crash(self):
        """Cut the power."""
        self.trigger_seen = True
        if self.args.rehearsal:
            self.killed_at = host_filetime()
            return
        if self.args.kill_cmd:
            try:
                subprocess.run(self.args.kill_cmd, shell=True, check=False,
                               timeout=30)
            except Exception as exc:      # noqa: BLE001 - report and continue
                self.log({"ev": "kill_cmd_failed", "error": str(exc)})
        elif self.args.kill_pid_file:
            # The guest process is started after the controller, so its pid
            # arrives through a file; it is read at the crash instant.
            try:
                with open(self.args.kill_pid_file, encoding="utf-8") as fh:
                    pid = int(fh.read().strip())
                os.kill(pid, 9)
                self.log({"ev": "guest_killed", "pid": pid})
            except Exception as exc:      # noqa: BLE001 - report and continue
                self.log({"ev": "kill_failed", "error": str(exc)})
        elif self.args.kill_pid:
            try:
                os.kill(self.args.kill_pid, 9)
            except OSError as exc:
                self.log({"ev": "kill_failed", "error": str(exc)})
        self.killed_at = host_filetime()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", self.args.port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print("port %d already in use" % self.args.port,
                      file=sys.stderr)
                return 2
            raise
        srv.listen(1)
        srv.settimeout(self.args.timeout)
        print("ctrl: listening on %d" % self.args.port, flush=True)
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            print("ctrl: no guest connected within %ds" % self.args.timeout,
                  file=sys.stderr)
            return 3
        print("ctrl: guest connected from %s" % (addr,), flush=True)
        conn.settimeout(self.args.timeout)
        buf = b""
        deadline = now() + self.args.timeout
        try:
            while now() < deadline:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        self.log({"ev": "unparsed", "raw": line[:400].decode(
                            "utf-8", "replace")})
                        continue
                    self.log(obj)
                    if self.args.crash_point and \
                            obj.get("ev") == self.args.crash_point:
                        # Fire the crash after the configured delay.
                        if self.args.delay_ms:
                            time.sleep(self.args.delay_ms / 1000.0)
                        self.trigger_crash()
                        break
                    if obj.get("ev") == "rehearsal_done":
                        break
                if self.trigger_seen:
                    break
        finally:
            try:
                conn.close()
            except OSError:
                pass
            srv.close()
        summary = self.write_manifest()
        print("ctrl: " + json.dumps(summary, ensure_ascii=False), flush=True)
        if not self.trigger_seen and not self.args.rehearsal:
            print("ctrl: crash point never reached", file=sys.stderr)
            return 3
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 host crash controller")
    ap.add_argument("--port", type=int, default=45000)
    ap.add_argument("--manifest", required=True,
                    help="host-side manifest (JSON lines)")
    ap.add_argument("--summary", default=None)
    ap.add_argument("--crash-point", default="flush_end",
                    help="probe event that triggers the crash")
    ap.add_argument("--delay-ms", type=int, default=0,
                    help="delay between the trigger event and the power-off")
    ap.add_argument("--kill-pid", type=int, default=0,
                    help="PID of the guest process to kill (SIGKILL)")
    ap.add_argument("--kill-pid-file", default="",
                    help="file holding the guest PID; read at the crash "
                         "instant (the guest starts after the controller)")
    ap.add_argument("--kill-cmd", default="",
                    help="command that performs the power-off")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--rehearsal", action="store_true",
                    help="record the trial but do not crash (pipeline test)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    return Controller(args).run()


if __name__ == "__main__":
    sys.exit(main())
