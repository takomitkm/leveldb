#!/usr/bin/env python3
"""E34 - turn rig artifacts into per-trial classification JSONs.

Inputs
  --work-dir   one subdirectory per trial, each holding
               manifest-trial.jsonl, manifest-recover.jsonl, preimage.json
  --namespaces JSONL from dump-namespaces.py (one line per trial with the
               raw, no-replay namespace entries)
  --out-dir    where to write <tag>.json (the e34-aggregate.py input)

The classification itself is e34-recover.py; this script only wires the
artifacts together so a whole cell can be processed in one call.

Usage:
    python e34-collect.py --work-dir work/B-create-large \
        --namespaces ns-B.jsonl --out-dir results/B-create-large
"""
import argparse
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_recover():
    path = os.path.join(HERE, "e34-recover.py")
    spec = importlib.util.spec_from_file_location("e34_recover", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_namespaces(path):
    """Map (arm, trial-number) -> list of names in the raw namespace.

    dump-namespaces.py tags look like "<cell>-<cell>-<n>", e.g.
    A-create-large-A-create-large-101, so the arm is the first component and
    the trial number the last.
    """
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            parts = row["tag"].split("-")
            names = [e.get("name") for e in (row.get("entries") or [])]
            out[(parts[0], parts[-1])] = names
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True,
                    help="dir with one subdir per trial tag")
    ap.add_argument("--namespaces", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    rec = load_recover()
    ns = load_namespaces(args.namespaces)
    os.makedirs(args.out_dir, exist_ok=True)

    tags = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(args.work_dir, "*"))
        if os.path.isdir(p))
    written = 0
    for tag in tags:
        if args.only and args.only not in tag:
            continue
        d = os.path.join(args.work_dir, tag)
        tm = os.path.join(d, "manifest-trial.jsonl")
        rm = os.path.join(d, "manifest-recover.jsonl")
        if not (os.path.exists(tm) and os.path.exists(rm)):
            print("%-30s MISSING-MANIFEST" % tag)
            continue
        info = rec.manifest_summary(rec.read_manifest(tm))
        vol = rec.check_recovery_report(rm, info)
        pre = None
        pre_path = os.path.join(d, "preimage.json")
        if os.path.exists(pre_path):
            with open(pre_path, encoding="utf-8") as fh:
                pre = json.load(fh)
        names = ns.get((info.get("arm"), tag.split("-")[-1]))
        pre_state = rec.check_pre_recovery(names, info) if names is not None else None
        verdict = rec.classify(info, pre, vol, True, pre_state)
        payload = {"manifest": tag, "trial": info, "volume_check": vol,
                   "pre_recovery_namespace": pre_state, "preimage": pre,
                   "verdict": verdict}
        with open(os.path.join(args.out_dir, tag + ".json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        written += 1
        print("%-30s %-3s present=%s source=%s"
              % (tag, verdict["class"],
                 (vol or {}).get("expected_present"),
                 verdict.get("recovery_source")))
    print("e34-collect: %d trials" % written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
