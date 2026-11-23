#!/usr/bin/env python3
"""E33 - ETW analyzer: per-arm DiskIo events with byte offsets and barriers.

tracerpt's CSV header leaves the DiskIo payload fields unnamed (the extra
columns after "User Data"), so this tool reads them by *name* from tracerpt's
XML rendering when the ETL is small (the logman Kernel-Disk sessions are a
few hundred KB), and otherwise reports counts only.  The XML schema observed
on build 26100:

  DiskIo / Write          : DiskNumber, IrpFlags, TransferSize, Reserved,
                            ByteOffset, FileObject, Irp, HighResResponseTime,
                            IssuingThreadId
  DiskIo / FlushBuffers   : DiskNumber, IrpFlags, HighResResponseTime, Irp,
                            IssuingThreadId

Output (JSON): per arm
  counts        : write / read / flush_buffers / flush_init totals
  writes        : [{t, disk, offset, size, irp}]  (only when XML was parsed)
  flushes       : [{t, disk, irp}]
  xml_parsed    : bool

Usage:
    python e33_etw_analyze.py --etw-dir DIR --out summary.json [--csv out.csv]
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
TRACE_NS = "http://schemas.microsoft.com/2004/08/events/trace"


def run_tracerpt(etl: str, out: str, fmt: str) -> bool:
    cmd = ["tracerpt", etl, "-o", out, "-of", fmt, "-y"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("tracerpt failed: %s" % exc, file=sys.stderr)
        return False
    if proc.returncode != 0:
        print("tracerpt rc=%d: %s" % (proc.returncode,
                                      (proc.stderr or proc.stdout)[:400]),
              file=sys.stderr)
        return False
    return os.path.exists(out)


def count_csv(path: str) -> dict:
    """Count DiskIo event types from a tracerpt CSV (header-driven)."""
    counts = {"write": 0, "read": 0, "flush_buffers": 0, "flush_init": 0,
              "other": 0}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd, None)
        if header is None:
            return counts
        header = [h.strip() for h in header]
        try:
            i_name = header.index("Event Name")
            i_type = header.index("Type")
        except ValueError:
            return counts
        for row in rd:
            if len(row) <= max(i_name, i_type):
                continue
            if row[i_name].strip() != "DiskIo":
                counts["other"] += 1
                continue
            t = row[i_type].strip()
            if t == "Write":
                counts["write"] += 1
            elif t == "Read":
                counts["read"] += 1
            elif t == "FlushBuffers":
                counts["flush_buffers"] += 1
            elif t == "FlushInit":
                counts["flush_init"] += 1
            else:
                counts["other"] += 1
    return counts


def _int(text):
    if text is None:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_xml(etl: str, max_writes: int = 20000) -> dict:
    """Parse tracerpt XML for named DiskIo payload fields."""
    tmp = tempfile.mktemp(suffix=".xml")
    out = {"writes": [], "flushes": [], "ok": False}
    if not run_tracerpt(etl, tmp, "XML"):
        return out
    try:
        for _event, elem in ET.iterparse(tmp, events=("end",)):
            if elem.tag != EVENT_NS + "Event":
                continue
            rend = elem.find(".//{%s}RenderingInfo" % EVENT_NS)
            if rend is None:
                elem.clear()
                continue
            prov = rend.find("{%s}Provider" % EVENT_NS)
            opcode = rend.find("{%s}Opcode" % EVENT_NS)
            prov_txt = prov.text.strip() if prov is not None and prov.text else ""
            op_txt = opcode.text.strip() if opcode is not None and opcode.text else ""
            if prov_txt != "DiskIo":
                elem.clear()
                continue
            data = {}
            for d in elem.findall(".//{%s}Data" % EVENT_NS):
                data[d.get("Name")] = (d.text or "").strip()
            ts = elem.find(".//{%s}TimeCreated" % EVENT_NS)
            tick = ts.get("SystemTime") if ts is not None else None
            if op_txt == "Write":
                if len(out["writes"]) < max_writes:
                    out["writes"].append({
                        "t": tick,
                        "disk": _int(data.get("DiskNumber")),
                        "offset": _int(data.get("ByteOffset")),
                        "size": _int(data.get("TransferSize")),
                        "irp": data.get("Irp"),
                    })
            elif op_txt == "FlushBuffers":
                out["flushes"].append({
                    "t": tick,
                    "disk": _int(data.get("DiskNumber")),
                    "irp": data.get("Irp"),
                })
            elem.clear()
        out["ok"] = True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 ETW analyzer")
    ap.add_argument("--etw-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--xml-max-bytes", type=int, default=24 << 20,
                    help="parse XML only for ETLs below this size")
    args = ap.parse_args()

    rows = []
    for name in sorted(os.listdir(args.etw_dir)):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".etl" or not name.startswith("arm-"):
            continue
        if not re.match(r"^arm-[A-Za-z]+$", stem):
            continue
        etl = os.path.join(args.etw_dir, name)
        arm = stem[len("arm-"):]
        entry = {"arm": arm, "etl": name, "size": os.path.getsize(etl)}
        csv_path = os.path.join(args.etw_dir, stem + ".csv")
        if run_tracerpt(etl, csv_path, "CSV"):
            entry.update({"counts": count_csv(csv_path),
                          "csv": os.path.basename(csv_path)})
        else:
            entry.update({"error": "tracerpt CSV failed"})
        if entry["size"] <= args.xml_max_bytes:
            parsed = parse_xml(etl)
            entry["xml_parsed"] = parsed["ok"]
            if parsed["ok"]:
                entry["writes"] = parsed["writes"]
                entry["flushes"] = parsed["flushes"]
        else:
            entry["xml_parsed"] = False
        rows.append(entry)

    payload = json.dumps({"arms": rows}, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh)
            keys = ["arm", "write", "read", "flush_buffers", "flush_init",
                    "xml_parsed", "error"]
            wr.writerow(keys)
            for r in rows:
                c = r.get("counts") or {}
                wr.writerow([r.get("arm"), c.get("write"), c.get("read"),
                             c.get("flush_buffers"), c.get("flush_init"),
                             r.get("xml_parsed"), r.get("error")])
    if not args.out and not args.csv:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
