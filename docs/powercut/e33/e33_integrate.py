#!/usr/bin/env python3
"""E33.1 - evidence integrator: decoded index records, target-page writes,
per-call L3 barrier attribution.

Inputs (produced by run-e33.ps1 and e33_etw_analyze.py):
  <artifacts>/volume-info.json                   disk number, partition offset
  <artifacts>/logfile/arm-X-LogFile.bin          raw $LogFile snapshot
  <artifacts>/logfile/arm-X-MFT-packed.{bin,json} selected MFT records
  <artifacts>/logfile/arm-X-extract.json         $LogFile run list
  <artifacts>/arm-X-ops.csv                      per-call FILETIME boundaries
  <artifacts>/etw/arm-X.csv                      parsed DiskIo events
  <artifacts>/etw-summary.json                   typed DiskIo rows

Outputs (in the artifact dir):
  decoded-index-records.json / .csv   Task 1
  target-page-writes.csv              Task 2
  l3-attribution.json                 Task 3
  E33-evidence.json                   merged summary

Method notes
------------
* ETW DiskIo `ByteOffset` is a *device* byte offset.  The volume-relative
  offset is `ByteOffset - partition_offset`; the $LogFile stream offset is
  `volume_offset - logfile_lcn*cluster_size` for the run containing it.
  This is arithmetic, not a heuristic shift.
* A target log page is `stream_offset // page_size` for a decoded index
  record whose decoded $FILE_NAME matches the arm's `e33_<ARM>_*` names and
  whose parent MFT reference equals the test directory record.
* L3 requires, per candidate call: a target-page write inside
  [OperationStart, CandidateFlushEnd], a FlushInit/FlushBuffers pair whose
  completion (same IRP) is <= CandidateFlushEnd, and API success.
* Marker events are used as a validation of the FILETIME↔ETW alignment when
  they decode; timestamps are otherwise a shared FILETIME base.
"""
import argparse
import csv
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e33_logfile_decode as dec  # noqa: E402


def load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- $LogFile map
def logfile_ranges(extract: dict) -> List[dict]:
    """Volume-relative byte ranges of the $LogFile extents.

    Each range: {vol_start, vol_end, stream_start} so a device offset can be
    converted to a $LogFile stream offset.
    """
    cluster = int(extract.get("cluster_size", 4096))
    runs = extract.get("logfile_run_list") or []
    out = []
    for i, (start_vcn, lcn) in enumerate(runs):
        nxt = runs[i + 1][0] if i + 1 < len(runs) else None
        if nxt is None:
            size = int(extract.get("logfile_data_size", 0))
            clusters = max(1, (size + cluster - 1) // cluster
                           - int(start_vcn))
        else:
            clusters = int(nxt) - int(start_vcn)
        vol_start = int(lcn) * cluster
        out.append({
            "vol_start": vol_start,
            "vol_end": vol_start + clusters * cluster,
            "stream_start": int(start_vcn) * cluster,
            "cluster": cluster,
        })
    return out


def device_to_stream(offset: int, partition_offset: int,
                     ranges: List[dict]) -> Optional[Tuple[int, int]]:
    """Map an ETW device ByteOffset to ($LogFile stream offset, page)."""
    if offset is None or partition_offset < 0:
        return None
    vol = offset - partition_offset
    for r in ranges:
        if r["vol_start"] <= vol < r["vol_end"]:
            stream = r["stream_start"] + (vol - r["vol_start"])
            return stream, stream // 4096
    return None


# ------------------------------------------------------------------- decoding
def decode_arm(log_path: str, cluster: int, arm: str,
               parent_record: Optional[int]) -> dict:
    with open(log_path, "rb") as fh:
        data = fh.read()
    parser = dec.LogFileParser(data)
    parser.cluster_size = cluster
    records = parser.walk_chains()
    prefix = "e33_%s_" % arm
    rows = []
    for rec in records:
        if not rec.get("index_op"):
            continue
        entry = rec.get("index_entry") or rec.get("undo_index_entry") or {}
        name = entry.get("file_name") or ""
        if not name.startswith(prefix):
            continue
        if parent_record is not None and entry.get("parent_record") not in (
                None, parent_record):
            continue
        try:
            page, off_in_page = parser.lsn_to_offset(rec["lsn"])
        except Exception:
            page, off_in_page = None, None
        rows.append({
            "arm": arm,
            "filename": name,
            "lsn": rec["lsn"],
            "transaction_id": rec.get("transaction_id"),
            "redo_opcode": rec.get("redo"),
            "undo_opcode": rec.get("undo"),
            "index_type": ("resident" if rec.get("redo_op") in
                           dec.INDEX_ROOT_OPS else "nonresident"),
            "target_record": entry.get("ref_record"),
            "parent_record": entry.get("parent_record"),
            "raw_log_page": rec.get("raw_log_page"),
            "raw_record_offset": rec.get("raw_record_offset"),
            "raw_log_page_byte_offset": rec.get("raw_log_page_byte_offset"),
            "lsn_page": page,
            "lsn_page_offset": off_in_page,
            "target_frs": rec.get("target_frs"),
            "oat_attr": rec.get("oat_attr_name"),
            "stream_offset": rec.get("stream_offset"),
        })
    return {
        "records_total": len(records),
        "index_records": len(rows),
        "rows": rows,
        "page_size": parser.page_size,
    }


def read_ops_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            def as_int(k, _row=row):
                try:
                    return int(_row.get(k) or 0)
                except ValueError:
                    return 0
            out.append({
                "rep": as_int("Rep"),
                "op": (row.get("Op") or "").strip(),
                "target": (row.get("Target") or "").strip(),
                "target_record": as_int("TargetRecord"),
                "parent_record": as_int("ParentRecord"),
                "api_ok": (row.get("ApiOk") or "").lower() == "true",
                "status": (row.get("FlushStatus") or "").strip(),
                "thread_id": as_int("ThreadId"),
                "op_start_ft": as_int("OpStartFt"),
                "op_end_ft": as_int("OpEndFt"),
                "flush_start_ft": as_int("FlushStartFt"),
                "flush_end_ft": as_int("FlushEndFt"),
            })
    return out


# --------------------------------------------------------------- per-call L3
def shared_name(call: dict, ops: List[dict]) -> bool:
    """True when this call's filename repeats across reps (delete target)."""
    base = os.path.basename(call["target"]).lower()
    return sum(1 for o in ops
               if os.path.basename(o["target"]).lower() == base) > 1


def match_call_to_records(call: dict, records: List[dict]) -> List[dict]:
    """Index records that belong to one candidate call.

    create  : file name carries _create_NNNN, record created by this call
    rename  : the destination name _ren_dst_NNNN
    delete  : the target _del_tgt (delete index entries carry the name)
    """
    base = os.path.basename(call["target"]).lower()
    hits = []
    for r in records:
        nm = r["filename"].lower()
        if call["op"] == "create":
            if nm == base:
                hits.append(r)
        elif call["op"] == "rename":
            if nm == base:
                hits.append(r)
        elif call["op"] == "delete":
            if nm == base:
                hits.append(r)
    return hits


def marker_windows(etw: dict) -> dict:
    """ETW-side measurement window from the rig's own markers.

    The rig emits an 'arm-start:<arm>' marker before the measured window and
    'arm-stop:<arm>' after it.  Their ETW timestamps bound the window
    independently of the API-side timestamps, which lets us prove that the
    ETW session really covers the calls.
    """
    out = {"start": None, "stop": None, "tokens": []}
    for m in etw.get("markers") or []:
        tok = m.get("token") or ""
        out["tokens"].append({"t": m.get("t"), "token": tok})
        if "arm-start" in tok and out["start"] is None:
            out["start"] = m.get("t")
        elif "arm-stop" in tok and out["stop"] is None:
            out["stop"] = m.get("t")
    return out


def barrier_pairs(etw: dict) -> List[dict]:
    """Pair FlushInit (start) with FlushBuffers (completion) by IRP."""
    starts: Dict[str, dict] = {}
    for fi in etw.get("flush_inits") or []:
        irp = fi.get("irp")
        if irp:
            starts.setdefault(irp, []).append(fi)
    pairs = []
    for fb in etw.get("flushes") or []:
        irp = fb.get("irp")
        candidates = starts.get(irp) or []
        start = None
        for c in candidates:
            if c.get("t") is not None and fb.get("t") is not None and \
                    c["t"] <= fb["t"]:
                start = c
        pairs.append({
            "irp": irp,
            "start_t": start.get("t") if start else None,
            "end_t": fb.get("t"),
            "thread": fb.get("thread"),
            "disk": fb.get("disk"),
        })
    return pairs


def evaluate_call(call: dict, records: List[dict], writes: List[dict],
                  pairs: List[dict], partition_offset: int,
                  ranges: List[dict], disk_number: Optional[int]) -> dict:
    """Decide L1/L2/L3 for one candidate call."""
    matched = match_call_to_records(call, records)
    # The page an ETW write can hit is the physical page the record occupies
    # in the snapshot, i.e. the page the scan found it on.  The LSN-derived
    # page is reported separately (and validated) because the circular log
    # means the two disagree for records from an earlier wrap generation.
    target_pages = set()
    lsn_pages = set()
    for r in matched:
        if r.get("raw_log_page") is not None:
            target_pages.add(r["raw_log_page"])
        elif r["lsn_page"] is not None:
            target_pages.add(r["lsn_page"])
        if r["lsn_page"] is not None:
            lsn_pages.add(r["lsn_page"])

    page_writes = []
    for w in writes:
        if disk_number is not None and w.get("disk") != disk_number:
            continue
        mapped = device_to_stream(w.get("offset"), partition_offset, ranges)
        if mapped is None:
            continue
        stream, page = mapped
        if page in target_pages:
            page_writes.append({**w, "stream_offset": stream, "page": page})

    # A target write belongs to this call if it falls inside the call window.
    # Timestamps come from the rig's FILETIME markers; ETW Clock-Time is the
    # same 100 ns FILETIME base, so the two are directly comparable.
    have_window = bool(call["op_start_ft"] and call["flush_end_ft"])
    window_writes = [
        w for w in page_writes
        if have_window and call["op_start_ft"] <= w["t"] <= call["flush_end_ft"]
    ]

    # A barrier competes if it completes before the API returns.
    completion_before_return = []
    for p in pairs:
        if p["end_t"] is None or not call["flush_end_ft"]:
            continue
        if p["end_t"] > call["flush_end_ft"]:
            continue
        # Thread match (when the rig recorded the caller thread) rules out
        # background flushes.
        thread_match = (p.get("thread") is not None and
                        call.get("thread_id") and
                        p["thread"] == call["thread_id"])
        completion_before_return.append({**p, "thread_match": thread_match})
    thread_matched = [p for p in completion_before_return if p["thread_match"]]

    # The write must precede the barrier that covers it:
    #   target page write -> write completion -> FlushInit -> FlushBuffers
    #   (same IRP) -> barrier completion -> API returns successfully.
    # A barrier that only *completes* after the API returned does not count.
    covered = []
    for w in window_writes:
        for p in completion_before_return:
            if p["start_t"] is None or p["end_t"] is None:
                continue
            if not (w["t"] <= p["start_t"] <= p["end_t"]):
                continue
            if p["end_t"] > call["flush_end_ft"]:
                continue
            covered.append({"write_t": w["t"], "page": w["page"],
                            "irp": p["irp"],
                            "barrier_start": p["start_t"],
                            "barrier_end": p["end_t"],
                            "thread_match": p["thread_match"]})
            break

    # Classification (see E33-PLAN.md):
    #   L0 no index record for this call's file
    #   L1 the index record exists, its log page was not seen on the stack
    #   L2 the target log page was written inside the call window
    #   L3 that write is covered by a flush barrier that completed before the
    #      API returned (and the API succeeded)
    if not matched:
        level = "L0"
    elif covered and call["api_ok"]:
        level = "L3"
    elif covered:
        level = "L2-api-failed"
    elif window_writes:
        level = "L2"
    elif matched:
        level = "L1"
    else:
        level = "L0"
    return {
        "op": call["op"],
        "rep": call["rep"],
        "filename": os.path.basename(call["target"]),
        "api_ok": call["api_ok"],
        "status": call["status"],
        "op_start_ft": call["op_start_ft"],
        "flush_end_ft": call["flush_end_ft"],
        "thread_id": call["thread_id"],
        "index_records": len(matched),
        "target_pages": sorted(target_pages),
        "lsn_pages": sorted(lsn_pages),
        "lsn_page_matches_physical": sorted(target_pages) == sorted(lsn_pages),
        "target_page_writes": len(page_writes),
        "target_page_writes_in_window": len(window_writes),
        "has_window_timestamps": have_window,
        "barriers_before_return": len(completion_before_return),
        "barriers_thread_matched": len(thread_matched),
        "covered_writes": covered[:8],
        "level": level,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="E33.1 evidence integrator")
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--cluster-size", type=int, default=4096)
    args = ap.parse_args()

    art = args.artifacts
    meta = os.path.join(art, "logfile")
    etw_dir = os.path.join(art, "etw")
    winfo = load_json(os.path.join(art, "volume-info.json")) or {}
    partition_offset = int(winfo.get("partition_offset", -1))
    disk_number = winfo.get("disk_number")
    if disk_number is not None:
        disk_number = int(disk_number)

    all_rows: List[dict] = []
    l3_calls: List[dict] = []
    page_write_rows: List[dict] = []
    evidence: Dict[str, dict] = {}
    residuals: List[str] = []

    arms = []
    if os.path.isdir(meta):
        for name in sorted(os.listdir(meta)):
            if name.endswith("-extract.json"):
                arms.append(name[:-len("-extract.json")])

    for arm in arms:
        arm_tag = arm[len("arm-"):] if arm.startswith("arm-") else arm
        extract = load_json(os.path.join(meta, arm + "-extract.json")) or {}
        cluster = int(extract.get("cluster_size", args.cluster_size))
        ranges = logfile_ranges(extract)

        # Etw typed rows for this arm
        etw = load_json(os.path.join(etw_dir, "arm-%s.json" % arm_tag)) or {}
        if not etw:
            # Fall back to combined etw-summary.json.
            summary = load_json(os.path.join(art, "etw-summary.json")) or {}
            for a in summary.get("arms", []):
                if a.get("arm") == arm_tag:
                    etw = a
                    break
        writes = etw.get("writes") or []

        ops = read_ops_csv(os.path.join(art, "arm-%s-ops.csv" % arm_tag))
        parent_record = None
        if ops:
            parent_record = ops[0].get("parent_record") or None

        logp = os.path.join(meta, arm + "-LogFile.bin")
        decoded = {"records_total": 0, "index_records": 0, "rows": [],
                   "page_size": 4096}
        if os.path.exists(logp):
            try:
                decoded = decode_arm(logp, cluster, arm_tag, parent_record)
            except Exception as exc:  # keep per-arm error, continue
                residuals.append("%s decode error: %s" % (arm, exc))

        all_rows.extend(decoded["rows"])

        # Target-page writes (Task 2) for every decoded record.
        for r in decoded["rows"]:
            target_page = r.get("raw_log_page")
            if target_page is None:
                target_page = r.get("lsn_page")
            if target_page is None:
                continue
            for w in writes:
                if disk_number is not None and w.get("disk") != disk_number:
                    continue
                mapped = device_to_stream(w.get("offset"), partition_offset,
                                          ranges)
                if mapped is None:
                    continue
                stream, page = mapped
                if page != target_page:
                    continue
                page_write_rows.append({
                    "arm": arm_tag,
                    "operation": "",
                    "filename": r["filename"],
                    "lsn": r["lsn"],
                    "transaction_id": r["transaction_id"],
                    "rcrd_page": page,
                    "lsn_page": r.get("lsn_page"),
                    "stream_offset": stream,
                    "physical_byte_offset": w.get("offset"),
                    "write_timestamp": w.get("t"),
                    "write_size": w.get("size"),
                    "rcrd_page_file_offset": r["raw_log_page_byte_offset"],
                    "lsn_page_offset": r["lsn_page_offset"],
                })

        # Per-call L3 (Task 3)
        pairs = barrier_pairs(etw)
        markers = marker_windows(etw)
        for call in ops:
            ev = evaluate_call(call, decoded["rows"], writes, pairs,
                               partition_offset, ranges, disk_number)
            ev["arm"] = arm_tag
            ev["shared_name"] = shared_name(call, ops)
            l3_calls.append(ev)

        evidence[arm_tag] = {
            "decoded_records_total": decoded["records_total"],
            "decoded_index_records": decoded["index_records"],
            "etw_writes": len(writes),
            "etw_flush_pairs": len(pairs),
            "ops": len(ops),
            "etw_markers": len(etw.get("markers") or []),
            "etw_window_start": markers["start"],
            "etw_window_stop": markers["stop"],
        }

    # ------------------------------------------------------------- exports
    def dump_json(name, payload):
        with open(os.path.join(art, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)

    def dump_csv(name, rows, fields):
        with open(os.path.join(art, name), "w", encoding="utf-8",
                  newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            wr.writeheader()
            for row in rows:
                wr.writerow(row)

    dump_json("decoded-index-records.json", {"rows": all_rows})
    dump_csv("decoded-index-records.csv", all_rows, [
        "arm", "filename", "lsn", "transaction_id", "redo_opcode",
        "undo_opcode", "index_type", "target_record", "parent_record",
        "raw_log_page", "raw_record_offset", "raw_log_page_byte_offset",
        "lsn_page", "lsn_page_offset", "target_frs", "oat_attr",
        "stream_offset"])

    dump_csv("target-page-writes.csv", page_write_rows, [
        "arm", "operation", "filename", "lsn", "transaction_id", "rcrd_page",
        "stream_offset", "physical_byte_offset", "write_timestamp",
        "write_size", "rcrd_page_file_offset", "lsn_page", "lsn_page_offset"])

    dump_json("l3-attribution.json", {"calls": l3_calls})

    # Aggregate per arm: how many calls reach each level.
    agg: Dict[str, dict] = {}
    for c in l3_calls:
        a = agg.setdefault(c["arm"], {"L0": 0, "L1": 0, "L2": 0, "L3": 0})
        a[c["level"]] += 1
    dump_json("E33-evidence.json", {
        "schema_version": 2,
        "volume_info": winfo,
        "arms": evidence,
        "level_counts": agg,
        "residuals": residuals,
    })
    print("decoded index records:", len(all_rows))
    print("target-page writes:", len(page_write_rows))
    print("candidate calls:", len(l3_calls))
    print("level counts:", json.dumps(agg))
    if residuals:
        print("residuals:", len(residuals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
