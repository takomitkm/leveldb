#!/usr/bin/env python3
"""E34 - aggregate trial outcomes per arm.

Reads the per-trial recovery JSONs produced by e34-recover.py and produces the
per-arm table required by E34-PLAN.md section 13, including the one-sided 95%
upper bound on the failure probability for arms with zero failures.

Usage:
    python e34-aggregate.py --trials out/*.json --out aggregate.json \
        [--csv aggregate.csv]
"""
import argparse
import csv
import glob
import json
import sys
from typing import Dict, List, Optional

CLASSES = ["R0", "R1", "R2", "R3", "R4", "RX"]


def upper_bound(n: int, confidence: float = 0.95) -> Optional[float]:
    """One-sided 95% upper bound on the failure probability after n trials
    with zero failures.

    P(zero failures) = (1 - p)^n must stay above (1 - confidence), so
    p <= 1 - (1 - confidence)^(1/n).  For n = 30 this is 9.5%, which is the
    rule-of-three 3/n the plan quotes; for n = 100 it is 3.0%.
    """
    if n <= 0:
        return None
    return round(1.0 - (1.0 - confidence) ** (1.0 / n), 4)


def load_trials(paths: List[str]) -> List[dict]:
    out = []
    for pattern in paths:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def arm_key(trial: dict) -> str:
    t = trial.get("trial") or {}
    return "%s/%s" % (t.get("arm") or "?", t.get("op") or "?")


def topology_key(trial: dict) -> str:
    t = trial.get("trial") or {}
    return "%s/%s" % (t.get("arm") or "?", t.get("dir") or "?")


def summarize(trials: List[dict]) -> dict:
    groups: Dict[str, List[dict]] = {}
    for tr in trials:
        groups.setdefault(arm_key(tr), []).append(tr)

    out: Dict[str, dict] = {}
    for key, group in sorted(groups.items()):
        counts = {c: 0 for c in CLASSES}
        lost = 0
        recovered = 0
        pre_states: Dict[str, int] = {}
        for tr in group:
            verdict = (tr.get("verdict") or {}).get("class") or "RX"
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict == "R1":
                lost += 1
            if verdict in ("R3", "R4"):
                recovered += 1
            pre = tr.get("preimage") or {}
            for st in pre.get("states", []):
                name = st.get("state")
                pre_states[name] = pre_states.get(name, 0) + 1
        valid = len(group) - counts["RX"]
        failures = counts["R0"] + counts["R1"] + counts["R2"]
        entry = {
            "trials": len(group),
            "valid_trials": valid,
            "classes": counts,
            "confirmed_lost": lost,
            "confirmed_recovered": recovered,
            "failure_probability_upper_bound_95": (
                upper_bound(valid) if valid and failures == 0 else None),
            "preimage_states": pre_states,
        }
        out[key] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E34 aggregate")
    ap.add_argument("--trials", nargs="+", required=True,
                    help="per-trial recovery JSON files (globs allowed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    trials = load_trials(args.trials)
    per_arm = summarize(trials)
    payload = {
        "trials_total": len(trials),
        "per_arm": per_arm,
        "note": "the 95% upper bound applies only to arms with zero failures "
                "and only to the tested conditions",
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["arm", "op", "trials", "valid", "R0", "R1", "R2",
                         "R3", "R4", "RX", "confirmed_lost",
                         "confirmed_recovered", "p_fail_upper95"])
            for key, e in sorted(per_arm.items()):
                arm, _, op = key.partition("/")
                c = e["classes"]
                wr.writerow([arm, op, e["trials"], e["valid_trials"],
                             c["R0"], c["R1"], c["R2"], c["R3"], c["R4"],
                             c["RX"], e["confirmed_lost"],
                             e["confirmed_recovered"],
                             e["failure_probability_upper_bound_95"]])

    print("e34-aggregate: %d trials, %d arm/op groups"
          % (len(trials), len(per_arm)))
    for key, e in sorted(per_arm.items()):
        print("  %-12s trials=%-4d valid=%-4d R3/R4=%-4d lost=%-4d ub95=%s"
              % (key, e["trials"], e["valid_trials"],
                 e["confirmed_recovered"], e["confirmed_lost"],
                 e["failure_probability_upper_bound_95"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
