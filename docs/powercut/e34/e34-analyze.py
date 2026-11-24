#!/usr/bin/env python3
"""E34 - pull rig artifacts and classify a trial matrix.

The rig host keeps every trial under e34-matrix/<arm>-<op>-<topo>/<tag>/:
a pre-recovery copy of the crashed disk, the trial manifest, and the
recovery manifest (the namespace Windows found after replaying the log).

This script pulls what it needs, runs the pre-recovery analysis on the
crashed image, and writes one classification per trial.

Usage:
    python e34-analyze.py --rig-dir <local copy of e34-matrix> \
        --out-dir results [--trials arm-op-topo-tag ...]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PREIMAGE = os.path.join(HERE, "e34-preimage.py")
NAMESPACE = os.path.join(HERE, "e34-namespace.py")
RECOVER = os.path.join(HERE, "e34-recover.py")

ENTRY_RE = re.compile(r"\{\"ev\":.*?\}\s*$")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def extract_manifest(image_path: str, out_path: str) -> int:
    """Pull the JSON event lines out of a FAT manifest volume image."""
    if not os.path.exists(image_path):
        return 0
    with open(image_path, "rb") as fh:
        data = fh.read()
    text = data.decode("latin-1")
    lines = []
    for m in re.finditer(r"\{\"ev\":.*?\}", text):
        lines.append(m.group(0))
    payload = "\n".join(lines)
    if payload:
        payload += "\n"
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
    return len(lines)


def find_trials(rig_dir: str):
    for arm_dir in sorted(os.listdir(rig_dir)):
        full = os.path.join(rig_dir, arm_dir)
        if not os.path.isdir(full):
            continue
        for tag in sorted(os.listdir(full)):
            trial_dir = os.path.join(full, tag)
            if not os.path.isdir(trial_dir):
                continue
            yield arm_dir, tag, trial_dir


def analyze_trial(trial_dir: str, work_dir: str) -> dict:
    tag = os.path.basename(trial_dir)
    out = {"tag": tag, "class": None, "reasons": [], "error": None}

    manifest = os.path.join(trial_dir, "manifest-trial.jsonl")
    manifest_recover = os.path.join(trial_dir, "manifest-recover.jsonl")
    if not os.path.exists(manifest):
        manifest = os.path.join(trial_dir, "logvol-trial.img")
        extract_manifest(manifest, os.path.join(work_dir, "manifest-trial.jsonl"))
        manifest = os.path.join(work_dir, "manifest-trial.jsonl")
    if not os.path.exists(manifest_recover):
        manifest_recover = os.path.join(trial_dir, "logvol-recover.img")
        extract_manifest(manifest_recover,
                         os.path.join(work_dir, "manifest-recover.jsonl"))
        manifest_recover = os.path.join(work_dir, "manifest-recover.jsonl")

    crashed = None
    for name in ("vm-prerecover.img", "vm.img", "vm-recover.img"):
        cand = os.path.join(trial_dir, name)
        if os.path.exists(cand):
            crashed = cand
            break
    if crashed is None:
        out["error"] = "no crashed image"
        out["class"] = "RX"
        return out
    out["crashed_image"] = os.path.basename(crashed)

    # The manifest tells the checker which directory to look at.
    info = {}
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("ev") == "trial_start":
                    info = ev
                if ev.get("ev") == "dir_info":
                    info["dir_record"] = ev.get("dir_record")
    name = info.get("name")
    dir_path = info.get("dir")

    preimage = os.path.join(work_dir, "preimage.json")
    pre_ns = os.path.join(work_dir, "pre-namespace.jsonl")
    cmd = [sys.executable, PREIMAGE, "--image", crashed,
           "--volume-offset", "1048576", "--out", preimage]
    if name:
        cmd += ["--expect", name]
    if info.get("dir_record"):
        cmd += ["--parent-record", str(info["dir_record"])]
    res = run(cmd)
    out["preimage_stderr"] = res.stderr.strip()[-400:]

    if dir_path:
        run([sys.executable, NAMESPACE, "--image", crashed, "--dir", dir_path,
             "--offset", "1048576", "--out", pre_ns])

    recovery_json = os.path.join(work_dir, "recovery.json")
    cmd = [sys.executable, RECOVER, "--manifest", manifest,
           "--out", recovery_json, "--preimage", preimage,
           "--recovery-report", manifest_recover]
    if os.path.exists(pre_ns):
        cmd += ["--pre-namespace", pre_ns]
    res = run(cmd)
    out["recover_stderr"] = res.stderr.strip()[-400:]
    if os.path.exists(recovery_json):
        with open(recovery_json, encoding="utf-8") as fh:
            payload = json.load(fh)
        out["class"] = payload["verdict"]["class"]
        out["reasons"] = payload["verdict"]["reasons"]
        out["recovery_source"] = payload["verdict"].get("recovery_source")
        out["trial"] = payload.get("trial")
        out["pre_recovery_namespace"] = payload.get("pre_recovery_namespace")
        # Keep the per-trial recovery JSON next to the other artifacts so
        # e34-aggregate.py can consume it unchanged.
        keep = os.path.join(trial_dir, "recovery.json")
        try:
            shutil.copyfile(recovery_json, keep)
        except OSError:
            pass
    else:
        out["error"] = res.stderr.strip()[-400:] or "classification failed"
        out["class"] = "RX"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rig-dir", required=True,
                    help="local copy of the rig's e34-matrix directory")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only", default="",
                    help="only analyse tags containing this substring")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    results = []
    for arm_dir, tag, trial_dir in find_trials(args.rig_dir):
        if args.only and args.only not in tag:
            continue
        work = tempfile.mkdtemp(prefix="e34-analyze-")
        try:
            result = analyze_trial(trial_dir, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        result["arm_dir"] = arm_dir
        results.append(result)
        print("%-28s %-4s %s" % (tag, result["class"],
                                 "; ".join(result["reasons"])[:120]))
        with open(os.path.join(args.out_dir, "trials.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)

    print("e34-analyze: %d trials" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
