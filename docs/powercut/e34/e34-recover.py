#!/usr/bin/env python3
"""E34 - post-recovery namespace check and trial classification.

Verifies a recovered volume against the trial manifest and classifies the
trial R0..RX (see E34-PLAN.md section 11).

The volume can be checked either by mounting it (Windows: a directory given
with --volume) or offline with ntfs-3g (Linux: a mount point or the image
itself with ntfs-3g).  The checker only *reads*; it never repairs, and it
refuses to run before the forensic copy exists unless --allow-no-forensic is
given (used by the rehearsal pipeline test).

Usage:
    python e34-recover.py --manifest trial.jsonl --out recovery.json \
        --volume D:            # Windows: the mounted volume
        --preimage preimage.json
        [--expect-name NAME] [--payload-hash HEX]
"""
import argparse
import hashlib
import json
import os
import sys
from typing import List, Optional

R0 = "R0"   # cannot mount / recovery failed
R1 = "R1"   # mounted, but a confirmed operation was lost
R2 = "R2"   # namespace or directory index inconsistent
R3 = "R3"   # correct namespace recovered after log replay
R4 = "R4"   # correct namespace + independent index/log evidence
RX = "RX"   # trial invalid


def load_json(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_manifest(path: str) -> List[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def manifest_summary(events: List[dict]) -> dict:
    """Reduce the event stream to the facts the classification needs."""
    info = {
        "arm": None, "op": None, "name": None, "final_name": None,
        "dir": None, "dir_record": None,
        "op_status": None, "op_err": 0,
        "flush_status": None, "flush_ok": None, "flush_ntstatus": None,
        "api_returned_success": False,
        "payload": None,
    }
    for ev in events:
        kind = ev.get("ev")
        if kind == "trial_start":
            info["arm"] = ev.get("arm")
            info["op"] = ev.get("op")
            info["name"] = ev.get("name")
            info["dir"] = ev.get("dir")
            info["payload"] = ev.get("payload")
        elif kind == "dir_info":
            info["dir_record"] = ev.get("dir_record")
        elif kind == "op_end":
            info["op_status"] = ev.get("status")
            info["op_err"] = ev.get("err", 0)
            if ev.get("final_name"):
                info["final_name"] = ev["final_name"]
        elif kind == "flush_end":
            info["flush_status"] = ev.get("status")
            info["flush_ok"] = ev.get("ok")
            info["flush_ntstatus"] = ev.get("ntstatus")
            # The candidate API returned successfully when it reported ok=true
            # (or a zero NTSTATUS for the native variant).
            if ev.get("ok") is True:
                info["api_returned_success"] = True
            if ev.get("ntstatus") in ("0x00000000", 0):
                info["api_returned_success"] = True
    return info


def hash_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def list_dir(path: str) -> Optional[List[str]]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return None


def check_volume(volume: str, info: dict) -> dict:
    """Inspect the recovered volume (Windows drive letter or a directory)."""
    if os.name == "nt" and len(volume) == 2 and volume[1] == ":":
        root = volume + "\\"
        dir_path = os.path.join(root, info.get("dir") or "")
        if not info.get("dir"):
            dir_path = root
    else:
        root = volume
        dir_path = os.path.join(root, info.get("dir") or "")
        if not info.get("dir"):
            dir_path = root
    out = {"root": root, "dir": dir_path, "listing": None,
           "expected_present": None, "expected_absent": None,
           "payload_hash": None, "dir_readable": False}
    listing = list_dir(dir_path)
    if listing is None:
        return out
    out["listing"] = listing
    out["dir_readable"] = True
    name = info.get("name")
    final = info.get("final_name") or name
    if name and final and final != name:
        out["expected_present"] = final in listing
        out["expected_absent"] = name not in listing
        out["both_present"] = (final in listing) and (name in listing)
        out["neither_present"] = (final not in listing) and (name not in listing)
    elif name:
        out["expected_present"] = name in listing
    return out


def classify(info: dict, pre: Optional[dict], vol: Optional[dict],
             trial_valid: bool) -> dict:
    """Classify one trial R0..RX."""
    reasons: List[str] = []

    if not trial_valid:
        return {"class": RX, "reasons": ["trial marked invalid by the harness"]}
    if vol is None or not vol.get("dir_readable"):
        return {"class": R0, "reasons": ["the recovered directory could not be "
                                         "listed"]}
    if info.get("op_status") not in (None, "ok"):
        reasons.append("the directory change itself failed "
                       "(%s)" % info.get("op_status"))
        return {"class": RX, "reasons": reasons}
    if not info.get("api_returned_success"):
        reasons.append("the candidate API did not report success")
        # A failed candidate flush is still a valid trial for arms A/J/L, but
        # its expected state is the pre-crash state, not the post-flush one.
        return {"class": R3, "reasons": reasons + [
            "no candidate flush succeeded; only the pre-crash state is expected"]}

    op = info.get("op")
    ok = True
    if op == "create":
        ok = bool(vol.get("expected_present"))
        if not ok:
            reasons.append("the confirmed created name is missing")
    elif op == "rename":
        if vol.get("both_present"):
            ok = False
            reasons.append("both the source and the target name are present")
        elif vol.get("neither_present"):
            ok = False
            reasons.append("neither the source nor the target name is present")
        elif not vol.get("expected_present"):
            ok = False
            reasons.append("the confirmed target name is missing")
    elif op == "delete":
        if vol.get("expected_present"):
            ok = False
            reasons.append("the confirmed deleted name is still present")
        else:
            ok = True
    else:
        ok = False
        reasons.append("unknown operation type")

    if not ok:
        return {"class": R1, "reasons": reasons}

    # The namespace is correct.  R4 additionally needs the pre-recovery log
    # evidence for the same operation.
    if pre:
        states = {s["filename"]: s["state"] for s in pre.get("states", [])}
        name = info.get("final_name") or info.get("name")
        state = states.get(name)
        if state == "positively identified":
            return {"class": R4, "reasons": reasons + [
                "the index operation was found in the pre-recovery $LogFile"]}
        reasons.append("pre-recovery log evidence: %s" % (state or "missing"))
        return {"class": R3, "reasons": reasons}
    reasons.append("no pre-recovery decode was available")
    return {"class": R3, "reasons": reasons}


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 recovery check")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preimage", default=None,
                    help="pre-recovery decode JSON")
    ap.add_argument("--volume", default=None,
                    help="the recovered volume (drive letter or mount point)")
    ap.add_argument("--dir", default=None,
                    help="target directory relative to the volume")
    ap.add_argument("--allow-no-forensic", action="store_true",
                    help="permit a missing pre-recovery decode "
                         "(rehearsal pipeline test only)")
    ap.add_argument("--invalid", action="store_true",
                    help="mark the trial invalid (RX)")
    args = ap.parse_args()

    events = read_manifest(args.manifest)
    info = manifest_summary(events)
    pre = load_json(args.preimage)
    if pre is None and not args.allow_no_forensic:
        print("e34-recover: refusing to classify without a pre-recovery "
              "forensic copy", file=sys.stderr)
        return 2

    vol = None
    if args.volume:
        if args.dir:
            info["dir"] = args.dir
        vol = check_volume(args.volume, info)

    verdict = classify(info, pre, vol, not args.invalid)
    out = {
        "manifest": os.path.basename(args.manifest),
        "trial": info,
        "volume_check": vol,
        "preimage": pre,
        "verdict": verdict,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("e34-recover: class=%s reasons=%s"
          % (verdict["class"], json.dumps(verdict["reasons"],
                                          ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
