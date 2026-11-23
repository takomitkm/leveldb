#!/usr/bin/env python3
"""E33 - ETW tracerpt CSV analyzer: per-arm write/flush counts and windows.

Consumes the tracerpt CSV files produced from the per-arm ETLs (wpr DiskIO)
and emits per-arm counts of Write / Read / FlushBuffers / FlushInit events,
plus a JSON summary. Optionally splits windows by the ops CSV timeline when
timestamps are available (tracerpt CSV time column is relative; the rig keeps
one session per arm, so whole-file counts are already window counts).

Usage:
    python e33_etw_analyze.py --etw-dir DIR --out summary.json [--csv out.csv]
"""
import argparse
import csv
import json
import os
import re
import sys
from typing import List, Optional


def sniff_dialect(path: str) -> Optional[csv.Dialect]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        sample = fh.read(4096)
    if not sample:
        return None
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t")
    except csv.Error:
        return None


def analyze_csv(path: str) -> dict:
    """Count event types in one tracerpt CSV.

    tracerpt CSV columns (default): TTime, TID, Processor, Instance, ...
    Event type name appears in one of the columns; we match by the
    `EventName`-like column or by substring on the row text.
    """
    counts = {
        "lines": 0,
        "write": 0,
        "read": 0,
        "flush_buffers": 0,
        "flush_init": 0,
        "other": 0,
        "irp_write_samples": [],
        "flush_samples": [],
    }
    dialect = sniff_dialect(path)
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, dialect) if dialect else csv.reader(fh)
        header = None
        for row in reader:
            if not row:
                continue
            counts["lines"] += 1
            if header is None:
                header = [c.strip().strip('"') for c in row]
                continue
            text = ",".join(row)
            # tracerpt renders the event name in the "Msg" / last columns for
            # the classic CSV format: e.g. "... , DiskIo, Write, ..."
            if re.search(r"(?i)\bWrite\b", text) and re.search(
                    r"(?i)DiskIo|atapi|scsi|stor", text):
                counts["write"] += 1
                if len(counts["irp_write_samples"]) < 3:
                    counts["irp_write_samples"].append(text[:220])
            elif re.search(r"(?i)\bRead\b", text) and re.search(
                    r"(?i)DiskIo|atapi|scsi|stor", text):
                counts["read"] += 1
            elif re.search(r"(?i)FlushBuffers", text):
                counts["flush_buffers"] += 1
                if len(counts["flush_samples"]) < 3:
                    counts["flush_samples"].append(text[:220])
            elif re.search(r"(?i)FlushInit", text):
                counts["flush_init"] += 1
            else:
                counts["other"] += 1
    counts.pop("lines", None)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 ETW analyzer")
    ap.add_argument("--etw-dir", required=True,
                    help="directory holding arm-*.etl (or pre-converted CSV)")
    ap.add_argument("--out", default=None, help="summary JSON output")
    ap.add_argument("--csv", default=None, help="per-arm counts CSV output")
    ap.add_argument("--tracerpt", default="tracerpt",
                    help="tracerpt binary name (default: tracerpt)")
    args = ap.parse_args()

    etw_dir = args.etw_dir
    rows: List[dict] = []
    for name in sorted(os.listdir(etw_dir)):
        if not name.startswith("arm-"):
            continue
        stem, ext = os.path.splitext(name)
        if ext.lower() == ".etl":
            csv_path = os.path.join(etw_dir, stem + ".csv")
            if not os.path.exists(csv_path):
                cmd = [args.tracerpt, os.path.join(etw_dir, name),
                       "-o", csv_path, "-of", "CSV", "-y"]
                rc = os.system(" ".join('"%s"' % c for c in cmd))
                if rc != 0 or not os.path.exists(csv_path):
                    rows.append({"arm": stem, "error": "tracerpt failed rc=%s" % rc})
                    continue
        elif ext.lower() == ".csv":
            csv_path = os.path.join(etw_dir, name)
        else:
            continue
        m = re.match(r"arm-([A-Za-z]+)", stem)
        arm = m.group(1) if m else stem
        counts = analyze_csv(csv_path)
        rows.append({"arm": arm, "file": os.path.basename(csv_path), **counts})

    out: dict = {"arms": rows}
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh)
            keys = ["arm", "write", "read", "flush_buffers", "flush_init",
                    "other", "error"]
            wr.writerow(keys)
            for r in rows:
                wr.writerow([r.get(k, "") for k in keys])
    if not args.out and not args.csv:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
