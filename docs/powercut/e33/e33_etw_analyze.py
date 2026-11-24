#!/usr/bin/env python3
"""E33.1 - reproducible ETW analyzer: typed DiskIo payloads from tracerpt CSV.

The E33 ETLs are 60-72 MB, so tracerpt XML rendering is impractical in CI.
This parser reads the *variable-length payload columns* that tracerpt CSV
appends after the "User Data" column, using field layouts validated against
tracerpt XML output from the same build (26100):

  DiskIo/Write        (28 cols, payload 19..27):
      DiskNumber, IrpFlags, TransferSize, Reserved, ByteOffset,
      FileObject, Irp, HighResResponseTime, IssuingThreadId
  DiskIo/Read         (28 cols) - same layout as Write
  DiskIo/FlushBuffers (24 cols, payload 19..23):
      DiskNumber, IrpFlags, HighResResponseTime, Irp, IssuingThreadId
  DiskIo/WriteInit, DiskIo/FlushInit (21 cols, payload 19..20):
      Irp, IssuingThreadId

Column 16 "Clock-Time" is a FILETIME (100 ns ticks since 1601-01-01), the
same time base used by the rig's GetSystemTimePreciseAsFileTime markers.

No XML conversion is needed.  Output JSON per arm:
  counts, writes[], flushes[], flush_inits[], write_inits[], markers[]
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

# The rig's own marker provider (manifest-free, so tracerpt shows the GUID).
MARKER_PROVIDER_GUID = "{e33c0de1-0000-4000-8000-00e33c0de100}"

# Payload column offsets into a tracerpt CSV row, validated against XML.
PAYLOAD_LAYOUT: Dict[str, Dict[str, int]] = {
    "Write": {"disk": 19, "flags": 20, "size": 21, "reserved": 22,
              "offset": 23, "file_object": 24, "irp": 25,
              "response": 26, "thread": 27},
    "Read": {"disk": 19, "flags": 20, "size": 21, "reserved": 22,
             "offset": 23, "file_object": 24, "irp": 25,
             "response": 26, "thread": 27},
    "FlushBuffers": {"disk": 19, "flags": 20, "response": 21,
                     "irp": 22, "thread": 23},
    "WriteInit": {"irp": 19, "thread": 20},
    "FlushInit": {"irp": 19, "thread": 20},
}


def _int(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except ValueError:
        return None


def _str(text: Optional[str]) -> Optional[str]:
    return text.strip() if text is not None else None


def _marker_token(row: List[str]) -> Optional[str]:
    """Recover the marker token from a rendered marker row, if present."""
    for cell in row:
        text = (cell or "").strip()
        if text.startswith("E33M|"):
            return text
        # tracerpt sometimes renders the payload as a hex blob.
        if len(text) > 32 and all(c in "0123456789abcdefABCDEF" for c in text):
            try:
                raw = bytes.fromhex(text)
                decoded = raw.decode("utf-16-le", errors="ignore")
            except ValueError:
                continue
            if "E33M|" in decoded:
                start = decoded.index("E33M|")
                return decoded[start:].split(chr(0))[0]
    return None


def etl_to_csv(etl: str, csv_path: str) -> bool:
    """Convert an ETL to CSV with tracerpt (used when no CSV exists yet)."""
    try:
        proc = subprocess.run(["tracerpt", etl, "-o", csv_path, "-of", "CSV",
                               "-y"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("tracerpt failed for %s: %s" % (etl, exc), file=sys.stderr)
        return False
    if proc.returncode != 0:
        print("tracerpt rc=%d for %s: %s"
              % (proc.returncode, etl, (proc.stderr or proc.stdout)[:300]),
              file=sys.stderr)
        return False
    return os.path.exists(csv_path)


def parse_csv(path: str, extra_columns: Optional[Dict[str, int]] = None) -> dict:
    """Parse one tracerpt CSV into typed DiskIo rows plus any custom marker rows.

    extra_columns maps a marker Event Name to a dict of field-name -> CSV
    column index, used for rig-emitted user-mode markers (e33 call boundaries).
    """
    counts = {"write": 0, "read": 0, "flush_buffers": 0, "flush_init": 0,
              "write_init": 0, "marker": 0, "other": 0}
    writes: List[dict] = []
    flushes: List[dict] = []
    flush_inits: List[dict] = []
    write_inits: List[dict] = []
    markers: List[dict] = []
    warnings: List[str] = []
    extra_columns = extra_columns or {}

    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd, None)
        if header is None:
            return {"counts": counts, "writes": writes, "flushes": flushes,
                    "flush_inits": flush_inits, "write_inits": write_inits,
                    "markers": markers, "warnings": ["empty CSV"]}
        header = [h.strip() for h in header]
        try:
            i_name = header.index("Event Name")
            i_type = header.index("Type")
            i_clock = header.index("Clock-Time")
        except ValueError:
            return {"counts": counts, "writes": writes, "flushes": flushes,
                    "flush_inits": flush_inits, "write_inits": write_inits,
                    "markers": markers,
                    "warnings": ["header lacks Event Name/Type/Clock-Time"]}

        for row in rd:
            if len(row) <= max(i_name, i_type, i_clock):
                continue
            name = row[i_name].strip()
            clock = _int(row[i_clock])

            # Marker events from the rig's own provider: a manifest-free
            # provider renders as its GUID in the Event Name column.
            if MARKER_PROVIDER_GUID in name:
                marker = {"marker": "E33", "t": clock,
                          "raw_name": name, "payload": row[i_name + 2]
                          if len(row) > i_name + 2 else ""}
                # A manifest-free payload is rendered as an opaque blob; try
                # to recover the UTF-16 token when tracerpt kept it.
                marker["token"] = _marker_token(row)
                markers.append(marker)
                counts["marker"] += 1
                continue

            if name in extra_columns:
                m = {"marker": name, "t": clock}
                for field, col in extra_columns[name].items():
                    m[field] = _int(row[col]) if col < len(row) else _str(
                        row[col] if col < len(row) else None)
                markers.append(m)
                counts["marker"] += 1
                continue

            if name != "DiskIo":
                counts["other"] += 1
                continue
            typ = row[i_type].strip()
            layout = PAYLOAD_LAYOUT.get(typ)
            if layout is None:
                counts["other"] += 1
                continue
            # Validate the row is long enough for this event's layout.
            if len(row) <= max(layout.values()):
                warnings.append("short %s row: %d cols" % (typ, len(row)))
                continue
            def get(key: str, _row=row, _layout=layout):
                return _row[_layout[key]] if key in _layout else None
            common = {"t": clock}
            if typ in ("Write", "Read"):
                common.update({
                    "disk": _int(get("disk")),
                    "flags": _str(get("flags")),
                    "size": _int(get("size")),
                    "offset": _int(get("offset")),
                    "file_object": _str(get("file_object")),
                    "irp": _str(get("irp")),
                    "response": _int(get("response")),
                    "thread": _int(get("thread")),
                })
                writes.append(common) if typ == "Write" else None
                if typ == "Write":
                    counts["write"] += 1
                else:
                    counts["read"] += 1
            elif typ == "FlushBuffers":
                common.update({
                    "disk": _int(get("disk")),
                    "flags": _str(get("flags")),
                    "response": _int(get("response")),
                    "irp": _str(get("irp")),
                    "thread": _int(get("thread")),
                })
                flushes.append(common)
                counts["flush_buffers"] += 1
            elif typ == "FlushInit":
                common.update({"irp": _str(get("irp")),
                               "thread": _int(get("thread"))})
                flush_inits.append(common)
                counts["flush_init"] += 1
            elif typ == "WriteInit":
                common.update({"irp": _str(get("irp")),
                               "thread": _int(get("thread"))})
                write_inits.append(common)
                counts["write_init"] += 1
    return {"counts": counts, "writes": writes, "flushes": flushes,
            "flush_inits": flush_inits, "write_inits": write_inits,
            "markers": markers, "warnings": warnings[:20]}


def main() -> int:
    ap = argparse.ArgumentParser(description="E33.1 reproducible ETW CSV parser")
    ap.add_argument("--etw-dir", required=True,
                    help="directory containing arm-*.csv")
    ap.add_argument("--out", default=None, help="output JSON")
    ap.add_argument("--csv", default=None, help="compact per-arm CSV")
    args = ap.parse_args()

    rows = []
    for name in sorted(os.listdir(args.etw_dir)):
        stem, ext = os.path.splitext(name)
        if not re.match(r"^arm-[A-Za-z0-9]+$", stem):
            continue
        if ext.lower() == ".etl":
            csv_name = stem + ".csv"
            csv_path = os.path.join(args.etw_dir, csv_name)
            if not os.path.exists(csv_path):
                if not etl_to_csv(os.path.join(args.etw_dir, name), csv_path):
                    rows.append({"arm": stem[len("arm-"):], "etl": name,
                                 "error": "tracerpt failed"})
                    continue
            name = csv_name
        elif ext.lower() != ".csv":
            continue
        arm = stem[len("arm-"):]
        result = parse_csv(os.path.join(args.etw_dir, name))
        if not any(result["counts"].values()):
            rows.append({"arm": arm, "csv": name, "error": "no events parsed"})
            continue
        rows.append({"arm": arm, "csv": name, **result})

    payload = json.dumps({"schema_version": 2,
                          "payload_layouts": PAYLOAD_LAYOUT,
                          "arms": rows}, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["arm", "write", "read", "flush_buffers",
                         "flush_init", "write_init", "marker", "warnings"])
            for r in rows:
                c = r["counts"]
                wr.writerow([r["arm"], c["write"], c["read"],
                             c["flush_buffers"], c["flush_init"],
                             c["write_init"], c["marker"],
                             ";".join(r["warnings"][:3])])
    if not args.out and not args.csv:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
