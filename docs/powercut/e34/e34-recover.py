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
        "candidate_flush_attempted": False,
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
            info["candidate_flush_attempted"] = True
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


def read_jsonl_names(path: str) -> Optional[List[str]]:
    """Read a namespace report produced by the recovery guest or the
    pre-recovery dumper.

    The guest reports the directory listing as JSON lines (one event per
    line), so the checker can work from the guest's own report instead of
    mounting the volume on the analysis host.  The pre-recovery dumper uses
    the "pre_entry" event name for the same purpose.
    """
    if not path or not os.path.exists(path):
        return None
    names: List[str] = []
    for ev in read_manifest(path):
        if ev.get("ev") in ("recover_entry", "pre_entry") and \
                ev.get("name") is not None:
            names.append(ev["name"])
    return sorted(names)


def check_recovery_report(report: str, info: dict) -> dict:
    """Build the volume check from the recovery guest's own report."""
    events = read_manifest(report)
    names = read_jsonl_names(report)
    result = None
    for ev in events:
        if ev.get("ev") == "recover_result":
            result = ev
    out = {"root": "guest-report", "dir": info.get("dir"), "listing": names,
           "expected_present": None, "expected_absent": None,
           "dir_readable": result is not None and bool(result.get("ok")),
           "report": result}
    if names is None:
        out["dir_readable"] = False
        return out
    name = info.get("name")
    final = info.get("final_name") or name
    if name and final and final != name:
        out["expected_present"] = final in names
        out["expected_absent"] = name not in names
        out["both_present"] = (final in names) and (name in names)
        out["neither_present"] = (final not in names) and (name not in names)
    elif name:
        out["expected_present"] = name in names
    return out


def check_pre_recovery(pre_namespace: Optional[List[str]],
                       info: dict) -> Optional[dict]:
    """What the raw (no-replay) namespace held before any mount.

    This is what separates "the flush put the index entry on the platter"
    from "the log replay had to bring it back".
    """
    if pre_namespace is None:
        return None
    name = info.get("name")
    final = info.get("final_name") or name
    out = {"listing": pre_namespace,
           "name_present": name in pre_namespace if name else None,
           "final_present": final in pre_namespace if final else None}
    if final and final != name:
        out["both_present"] = (final in pre_namespace) and (name in pre_namespace)
        out["neither_present"] = (final not in pre_namespace) and \
            (name not in pre_namespace)
    return out


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
             trial_valid: bool, pre_state: Optional[dict] = None) -> dict:
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
    # Arm A has no candidate flush by definition.  Its no-op reports ok:true,
    # so arm identity -- not the status field -- is what marks it as a control.
    # A missing entry is still a confirmed-successful namespace operation that
    # was lost (R1); a surviving entry is a genuine recovery observation.
    is_control = (info.get("arm") == "A") or \
        not info.get("candidate_flush_attempted")
    if is_control:
        reasons.append("control arm A (no candidate flush)")
    elif not info.get("api_returned_success"):
        # The candidate flush ran and failed.  The guest therefore knew the
        # operation was not durable, so it is not a "confirmed-successful
        # operation that was lost"; only the pre-crash state is expected.
        reasons.append("the candidate API did not report success")
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

    # The namespace is correct.  How it got there matters for the E34
    # conclusion, so record it: "already durable" means the index entry was
    # on the platter before the power loss, "replayed" means the raw
    # namespace did not carry it and the log replay restored it.
    recovery_source = "unknown"
    if pre_state is not None:
        if op == "create":
            recovery_source = ("already durable" if pre_state.get("final_present")
                               else "replayed")
        elif op == "rename":
            recovery_source = ("already durable" if pre_state.get("final_present")
                               else "replayed")
        elif op == "delete":
            recovery_source = ("already durable" if not pre_state.get("final_present")
                               else "replayed")
        reasons.append("pre-recovery raw namespace: %s" %
                       ("the confirmed state was already on the platter"
                        if recovery_source == "already durable"
                        else "the confirmed state was absent before the replay"))

    # R4 additionally needs the pre-recovery log evidence for the same
    # operation.
    if pre:
        states = {s["filename"]: s["state"] for s in pre.get("states", [])}
        name = info.get("final_name") or info.get("name")
        state = states.get(name)
        if state == "positively identified":
            return {"class": R4, "reasons": reasons + [
                "the index operation was found in the pre-recovery $LogFile"],
                "recovery_source": recovery_source}
        reasons.append("pre-recovery log evidence: %s" % (state or "missing"))
        return {"class": R3, "reasons": reasons, "recovery_source": recovery_source}
    reasons.append("no pre-recovery decode was available")
    return {"class": R3, "reasons": reasons, "recovery_source": recovery_source}


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 recovery check")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preimage", default=None,
                    help="pre-recovery decode JSON")
    ap.add_argument("--volume", default=None,
                    help="the recovered volume (drive letter or mount point)")
    ap.add_argument("--recovery-report", default=None,
                    help="JSONL report from the recovery guest (the "
                         "namespace listing after Windows replayed the log)")
    ap.add_argument("--pre-namespace", default=None,
                    help="JSONL report of the raw namespace BEFORE any mount")
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
    if args.recovery_report:
        vol = check_recovery_report(args.recovery_report, info)
    elif args.volume:
        if args.dir:
            info["dir"] = args.dir
        vol = check_volume(args.volume, info)

    pre_state = None
    if args.pre_namespace:
        pre_state = check_pre_recovery(read_jsonl_names(args.pre_namespace),
                                       info)

    verdict = classify(info, pre, vol, not args.invalid, pre_state)
    out = {
        "manifest": os.path.basename(args.manifest),
        "trial": info,
        "volume_check": vol,
        "pre_recovery_namespace": pre_state,
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
