#!/usr/bin/env python3
"""E34 - classify every trial of a matrix run and aggregate per arm.

Inputs per trial (pulled from the rig):
  <work>/<tag>/manifest-trial.jsonl     the external manifest from E34LOG
  <work>/<tag>/manifest-recover.jsonl   the namespace Windows saw after replay
  <work>/<tag>/preimage.json            the pre-recovery $LogFile decode
  <work>/<tag>/pre-namespace.jsonl      the raw (no-replay) namespace

The raw namespace needs the 4 GiB pre-recovery image, so those are pulled,
used and deleted one at a time (see dump-namespaces.py).

Usage:
    python e34-classify-all.py --work-dir <dir> --out-dir <dir> [--only P]
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECOVER = os.path.join(HERE, "e34-recover.py")
AGGREGATE = os.path.join(HERE, "e34-aggregate.py")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True,
                    help="dir with one subdir per trial tag")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    tags = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(args.work_dir, "*"))
        if os.path.isdir(p) and not os.path.basename(p).startswith("img"))
    rows = []
    for tag in tags:
        if args.only and args.only not in tag:
            continue
        d = os.path.join(args.work_dir, tag)
        tm = os.path.join(d, "manifest-trial.jsonl")
        rm = os.path.join(d, "manifest-recover.jsonl")
        pre = os.path.join(d, "preimage.json")
        pre_ns = os.path.join(d, "pre-namespace.jsonl")
        if not (os.path.exists(tm) and os.path.exists(rm)):
            print("%-30s MISSING-MANIFEST" % tag)
            continue
        out = os.path.join(args.out_dir, tag + ".json")
        cmd = [sys.executable, RECOVER, "--manifest", tm, "--out", out]
        cmd += (["--preimage", pre] if os.path.exists(pre)
                else ["--allow-no-forensic"])
        cmd += ["--recovery-report", rm]
        if os.path.exists(pre_ns):
            cmd += ["--pre-namespace", pre_ns]
        r = run(cmd)
        line = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1]
        print("%-30s %s" % (tag, line))
        rows.append({"tag": tag, "result": line})
        with open(os.path.join(args.work_dir, "progress.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)

    r = run([sys.executable, AGGREGATE, "--trials",
             os.path.join(args.out_dir, "*.json"),
             "--out", os.path.join(args.out_dir, "aggregate.json")])
    print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip()[-400:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
