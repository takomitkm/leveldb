#!/usr/bin/env python3
"""E33 - results integrator: decode extracted $LogFile images and combine with
the ETW counts into the per-arm evidence table.

For each arm (arm-X-MFT.bin + arm-X-LogFile.bin in the logfile dir):
  1. locate the fixture directories' MFT record numbers via e33_mft
     (names e33_<ARM>_*, plus the fixture dirs 'small'/'large');
  2. run the $LogFile decoder over the extracted image;
  3. attribute index operations (Add/DeleteIndexEntry*, filename matches) to
     the target fixtures;
  4. merge with the ETW per-arm counts and classify L0..L3 per the plan:
       L0 no attributable index records at all;
       L1 target $I30 opcodes exist in the log;
       L2 needs ETW target-page evidence (not derivable here; reserved);
       L3 needs barrier evidence (reserved for ETW correlation);
  The integrator reports the raw evidence; the final L-level judgment stays
  in E33-RESULTS.md.

Usage:
    python e33_integrate.py --artifacts e33-artifacts-... --out E33-evidence.json
"""
import argparse
import json
import os
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e33_logfile_decode as dec  # noqa: E402
import e33_mft  # noqa: E402


def decode_arm(logfile_path: str, cluster_size: Optional[int]) -> dict:
    with open(logfile_path, "rb") as fh:
        data = fh.read()
    parser = dec.LogFileParser(data)
    if cluster_size:
        parser.cluster_size = cluster_size
    records = parser.walk_chains()
    summary = dec.summarize(records)
    return {
        "records_total": summary["records_total"],
        "records_index": summary["records_index"],
        "ops_total": summary["ops_total"],
        "ops_index": summary["ops_index"],
        "index_names": summary["index_names"],
        "index_records": summary["index_records"][:400],
    }


def fixture_context(mft_path: str, cluster_size: int) -> dict:
    with open(mft_path, "rb") as fh:
        data = fh.read()
    m = e33_mft.MftImage(data, cluster_size)
    out: dict = {
        "cluster_size": m.cluster_size,
        "record_size": m.record_size,
        "records_total": m.count,
        "logfile_lcn_count": 0,
        "logfile_lcn_first": [],
        "fixtures": {},
    }
    lcns = m.logfile_lcns()
    if lcns:
        out["logfile_lcn_count"] = len(lcns)
        out["logfile_lcn_first"] = lcns[:16]
    for name in ("small", "large"):
        hits = m.find_by_name(name)
        for n in hits:
            ctx = m.index_context(n)
            if ctx:
                out["fixtures"]["%s#%d" % (name, n)] = ctx
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E33 results integrator")
    ap.add_argument("--artifacts", required=True,
                    help="e33-artifacts-* directory from run-e33.ps1")
    ap.add_argument("--out", default=None, help="output JSON path")
    ap.add_argument("--cluster-size", type=int, default=4096)
    args = ap.parse_args()

    meta_dir = os.path.join(args.artifacts, "logfile")
    if not os.path.isdir(meta_dir):
        print("no logfile dir under %s" % args.artifacts)
        return 2
    arms: List[str] = []
    for name in sorted(os.listdir(meta_dir)):
        m = None
        if name.endswith("-MFT.bin"):
            m = name[:-len("-MFT.bin")]
        if m and m not in arms:
            arms.append(m)

    result: dict = {"cluster_size": args.cluster_size, "arms": {}}
    for arm in arms:
        mft_path = os.path.join(meta_dir, arm + "-MFT.bin")
        log_path = os.path.join(meta_dir, arm + "-LogFile.bin")
        entry: dict = {"mft": os.path.basename(mft_path),
                       "logfile": os.path.basename(log_path)}
        if os.path.exists(mft_path):
            try:
                entry["mft_context"] = fixture_context(mft_path, args.cluster_size)
            except Exception as exc:  # keep going; report per-arm
                entry["mft_context_error"] = str(exc)
        if os.path.exists(log_path):
            try:
                entry["logfile"] = decode_arm(log_path, args.cluster_size)
                # Trim heavy fields.
                entry["logfile"]["index_records"] = [
                    {k: v for k, v in rec.items()
                     if k not in ("lcns", "index_entry")}
                    for rec in entry["logfile"]["index_records"][:200]]
            except Exception as exc:
                entry["logfile_error"] = str(exc)
        result["arms"][arm] = entry

    # Cross-arm comparison: which arms carry target-name index records?
    target_names: Dict[str, set] = {}
    for arm, entry in result["arms"].items():
        lf = entry.get("logfile") or {}
        names = lf.get("index_names") or {}
        arm_names = set()
        for nm in names:
            if "e33_" in nm or nm in ("small", "large"):
                arm_names.add(nm)
        target_names[arm] = arm_names
    result["target_index_names_by_arm"] = {k: sorted(v)
                                           for k, v in target_names.items()}

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
