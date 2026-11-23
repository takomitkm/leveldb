#!/usr/bin/env python3
"""E33 - results integrator: decode $LogFile images, join with ETW offsets.

Inputs (produced by run-e33.ps1 + e33_etw_analyze.py):
  <artifacts>/logfile/arm-X-LogFile.bin          raw $LogFile snapshot
  <artifacts>/logfile/arm-X-MFT-packed.bin       selected MFT records
  <artifacts>/logfile/arm-X-MFT-packed.json      record number map
  <artifacts>/logfile/arm-X-extract.json         $LogFile run list, cluster size
  <artifacts>/etw-summary.json                   DiskIo writes/flushes per arm

Per arm it reports:
  * decoded index records attributable to the test fixtures (name + parent
    reference matches);
  * Counter A - $LogFile pages written in the measured window (writes landing
    in the $LogFile run list), and which decoded target LSN pages they cover;
  * Counter B - home-page writes (writes landing in the fixture directory's
    MFT record cluster or its $INDEX_ALLOCATION extents);
  * barrier coverage: for each target write, whether a successful
    DiskIo/FlushBuffers event follows it inside the window.

The L-level verdict is NOT assigned here; that stays in E33-RESULTS.md.
"""
import argparse
import json
import os
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e33_logfile_decode as dec  # noqa: E402
import e33_mft  # noqa: E402


def load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def decode_arm(log_path: str, cluster_size: int) -> dict:
    with open(log_path, "rb") as fh:
        data = fh.read()
    parser = dec.LogFileParser(data)
    parser.cluster_size = cluster_size
    records = parser.walk_chains()
    summary = dec.summarize(records)
    page_size = parser.page_size
    slim = []
    for r in summary["index_records"]:
        try:
            page_no, _off = parser.lsn_to_offset(r["lsn"])
        except Exception:
            page_no = None
        item = {
            "lsn": r["lsn"],
            "page": page_no,
            "page_byte_offset": (page_no * page_size) if page_no is not None else None,
            "txn": r["transaction_id"],
            "redo": r["redo"],
            "undo": r["undo"],
            "target_frs": r.get("target_frs"),
            "oat_record": r.get("oat_record"),
            "oat_attr": r.get("oat_attr_name"),
            "target_kind": r.get("target_kind"),
            "stream_offset": r.get("stream_offset"),
        }
        for key in ("index_entry", "undo_index_entry"):
            ent = r.get(key)
            if ent:
                item[key] = {
                    "name": ent.get("file_name"),
                    "parent_record": ent.get("parent_record"),
                    "child_record": ent.get("ref_record"),
                    "flags": ent.get("flags"),
                }
        slim.append(item)
    return {
        "records_total": summary["records_total"],
        "records_index": summary["records_index"],
        "ops_index": summary["ops_index"],
        "index_names": summary["index_names"],
        "page_size": page_size,
        "index_records": slim,
    }


def mft_context(packed_bin: str, packed_json: dict) -> dict:
    with open(packed_bin, "rb") as fh:
        data = fh.read()
    cluster = packed_json.get("cluster_size",
                              packed_json.get("boot", {}).get("cluster_size", 4096))
    m = e33_mft.MftImage.from_packed(data, packed_json.get("numbers", []),
                                     packed_json.get("record_size", 1024),
                                     cluster)
    out = {"fixtures": {}, "named": {}}
    for n in packed_json.get("numbers", []):
        ctx = m.index_context(n)
        if not ctx:
            continue
        out["fixtures"][str(n)] = ctx
        nm = ctx.get("file_name")
        if nm:
            out["named"][nm] = n
    return out


def classify_writes(writes: List[dict], cluster: int,
                    logfile_runs: List[List[int]],
                    index_extent_bytes: List[int],
                    mft_record_bytes: List[int]) -> dict:
    """Split writes into $LogFile / home-page / other, per volume ranges."""
    log_spans = []
    for i, (start_vcn, lcn) in enumerate(logfile_runs):
        nxt = logfile_runs[i + 1][0] if i + 1 < len(logfile_runs) else None
        if nxt is None:
            nxt = start_vcn + 1
        log_spans.append((lcn * cluster, (lcn + (nxt - start_vcn)) * cluster,
                          start_vcn * cluster))
    home_ranges = []
    for b in mft_record_bytes:
        home_ranges.append((b, b + 1024, "mft-record"))
    for b in index_extent_bytes:
        home_ranges.append((b, b + cluster, "index-allocation"))

    out = {"logfile_writes": [], "home_writes": [], "other_count": 0}
    for w in writes:
        off = w.get("offset")
        if off is None:
            continue
        hit_log = None
        for vol_start, vol_end, stream_start in log_spans:
            if vol_start <= off < vol_end:
                hit_log = stream_start + (off - vol_start)
                break
        if hit_log is not None:
            out["logfile_writes"].append({"t": w.get("t"), "offset": off,
                                          "stream_offset": hit_log,
                                          "size": w.get("size")})
            continue
        hit_home = None
        for start, end, kind in home_ranges:
            if start <= off < end:
                hit_home = kind
                break
        if hit_home:
            out["home_writes"].append({"t": w.get("t"), "offset": off,
                                       "kind": hit_home,
                                       "size": w.get("size")})
        else:
            out["other_count"] += 1
    return out


def barrier_after(flushes: List[dict], t: Optional[str]) -> Optional[bool]:
    if not flushes:
        return None
    if not t:
        return None
    for f in flushes:
        ft = f.get("t")
        if ft and ft > t:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 results integrator")
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cluster-size", type=int, default=4096)
    args = ap.parse_args()

    meta = os.path.join(args.artifacts, "logfile")
    etw = load_json(os.path.join(args.artifacts, "etw-summary.json")) or {"arms": []}
    etw_by_arm = {e.get("arm"): e for e in etw.get("arms", [])}

    result: dict = {"arms": {}}
    arms = []
    if os.path.isdir(meta):
        for name in sorted(os.listdir(meta)):
            if name.endswith("-extract.json"):
                arms.append(name[:-len("-extract.json")])
    for arm in arms:
        entry: dict = {}
        extract = load_json(os.path.join(meta, arm + "-extract.json")) or {}
        cluster = extract.get("cluster_size", args.cluster_size)
        runs = extract.get("logfile_run_list") or []
        entry["extract"] = {k: extract.get(k) for k in
                            ("logfile_runs", "logfile_data_size",
                             "logfile_bytes_read", "records_packed",
                             "cluster_size")}
        packed_json = load_json(os.path.join(meta, arm + "-MFT-packed.json"))
        ctx = None
        if packed_json:
            try:
                ctx = mft_context(os.path.join(meta, arm + "-MFT-packed.bin"),
                                  packed_json)
            except Exception as exc:
                entry["mft_error"] = str(exc)
        if ctx:
            entry["mft_fixtures"] = {
                k: {"file_name": v.get("file_name"),
                    "i30_nonresident": v.get("i30_nonresident"),
                    "index_alloc_size": (v.get("index_alloc") or {}).get("alloc_size"),
                    "index_alloc_runs": (v.get("index_alloc") or {}).get("lcns")}
                for k, v in ctx["fixtures"].items()}
        log_path = os.path.join(meta, arm + "-LogFile.bin")
        lf = None
        if os.path.exists(log_path):
            try:
                lf = decode_arm(log_path, cluster)
            except Exception as exc:
                entry["logfile_error"] = str(exc)
        if lf:
            entry["logfile"] = {k: lf[k] for k in
                                ("records_total", "records_index",
                                 "ops_index", "index_names",
                                 "page_size")}
            entry["index_records"] = lf["index_records"][:200]

        etw_arm = etw_by_arm.get(arm[len("arm-"):]) or {}
        entry["etw_counts"] = etw_arm.get("counts")
        entry["etw_xml_parsed"] = etw_arm.get("xml_parsed")
        writes = etw_arm.get("writes") or []
        flushes = etw_arm.get("flushes") or []
        if writes and ctx and runs:
            index_extents = []
            for v in ctx["fixtures"].values():
                runs_i = (v.get("index_alloc") or {}).get("lcns") or []
                index_extents.extend([lcn * cluster for lcn in runs_i])
            mft_map = (packed_json or {}).get("record_volume_offsets") or {}
            mft_record_bytes = [int(off) for off in mft_map.values()]
            classified = classify_writes(writes, cluster, runs,
                                         index_extents, mft_record_bytes)
            entry["counter_A_logfile_writes"] = len(classified["logfile_writes"])
            entry["counter_B_home_writes"] = len(classified["home_writes"])
            entry["other_writes"] = classified["other_count"]
            entry["logfile_write_samples"] = classified["logfile_writes"][:20]
            entry["home_write_samples"] = classified["home_writes"][:20]
            last_write_t = None
            for w in classified["logfile_writes"] + classified["home_writes"]:
                if w.get("t"):
                    if last_write_t is None or w["t"] > last_write_t:
                        last_write_t = w["t"]
            entry["barrier_after_target_writes"] = barrier_after(flushes,
                                                                 last_write_t)
            entry["flush_count"] = len(flushes)
        result["arms"][arm] = entry

    payload = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        print("wrote %s" % args.out)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
