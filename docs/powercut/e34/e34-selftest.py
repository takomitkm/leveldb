#!/usr/bin/env python3
"""E34 - self-tests for the analysis pipeline (no crash required).

These tests pin down the parts of E34 that do not need a power cut:

  * the external channel protocol: the controller's manifest reduction and the
    probe's event vocabulary;
  * the clock alignment between the guest's FILETIME and the host clock;
  * the four-state vocabulary of the pre-recovery decode;
  * the R0..RX classification for every combination the plan lists;
  * the 3/N statistical bound.

Run:
    python e34-selftest.py
Exit code 0 = all tests pass.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crashctl = _load("e34_crashctl", os.path.join(HERE, "e34-crashctl.py"))
recover = _load("e34_recover", os.path.join(HERE, "e34-recover.py"))
aggregate = _load("e34_aggregate", os.path.join(HERE, "e34-aggregate.py"))
preimage = _load("e34_preimage", os.path.join(HERE, "e34-preimage.py"))

FAILURES = []


def check(name, ok, detail=""):
    if ok:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


# --------------------------------------------------------------- controller
def test_controller_manifest():
    print("controller manifest reduction")
    events = [
        {"ev": "trial_start", "t": 1000000, "arm": "C", "op": "create",
         "name": "e34.bin", "dir": "D:\\e34", "payload": 4096},
        {"ev": "dir_info", "t": 1000100, "dir_record": 815},
        {"ev": "op_start", "t": 1000200},
        {"ev": "op_end", "t": 1005000, "status": "ok", "file_record": 900},
        {"ev": "flush_start", "t": 1005100},
        {"ev": "flush_end", "t": 1009000, "status": "win32-dir", "ok": True,
         "err": 0},
        {"ev": "trial_end", "t": 1009100, "status": "ok", "err": 0,
         "final_name": "e34.bin"},
    ]
    info = recover.manifest_summary(events)
    check("op status is captured", info["op_status"] == "ok", info["op_status"])
    check("dir record is captured", info["dir_record"] == 815)
    check("api success is detected", info["api_returned_success"] is True)
    check("arm/op are captured", (info["arm"], info["op"]) == ("C", "create"))


def test_controller_native_status():
    print("controller native NTSTATUS success")
    events = [{"ev": "flush_end", "t": 1, "status": "ntex-i30-8",
               "ntstatus": "0x00000000"}]
    info = recover.manifest_summary(events)
    check("NTSTATUS 0 counts as success", info["api_returned_success"] is True)


def test_controller_clock_offset():
    print("controller clock alignment")
    import argparse
    args = argparse.Namespace(port=0, manifest="x", summary=None,
                              crash_point="flush_end", delay_ms=0,
                              kill_pid=0, kill_cmd="", timeout=5,
                              rehearsal=True, verbose=False)
    ctl = crashctl.Controller(args)
    # A guest event whose FILETIME is 1 second behind the host clock.
    guest_t = crashctl.host_filetime() - 10_000_000
    ctl.log({"ev": "trial_start", "t": guest_t})
    check("offset is measured", ctl.guest_offset is not None)
    check("offset is about one second",
          abs(ctl.guest_offset - 10_000_000) < 2_000_000,
          ctl.guest_offset)
    ov = ctl.measure_overhead()
    check("channel overhead is reported", ov is not None and ov["samples"] == 1,
          ov)


def test_controller_kill_pid_file():
    print("controller crash trigger through a pid file")
    import argparse
    import os
    import tempfile
    args = argparse.Namespace(port=0, manifest="x", summary=None,
                              crash_point="flush_end", delay_ms=0,
                              kill_pid=0, kill_pid_file="", kill_cmd="",
                              timeout=5, rehearsal=False, verbose=False)
    ctl = crashctl.Controller(args)
    # A pid file that does not exist must be reported, not crash the trial.
    args.kill_pid_file = os.path.join(tempfile.mkdtemp(), "no-such-pid")
    ctl.trigger_crash()
    kinds = [e.get("ev") for e in ctl.events]
    check("a missing pid file is reported",
          "kill_failed" in kinds, kinds)
    check("the crash trigger is still recorded", ctl.trigger_seen is True)
    check("the kill timestamp is recorded", ctl.killed_at is not None)


# ------------------------------------------------------------ pre-recovery
def test_preimage_states():
    print("pre-recovery state vocabulary")
    records = [
        {"index_op": True,
         "index_entry": {"file_name": "e34.bin", "parent_record": 815},
         "undo": None, "redo": "AddIndexEntryRoot", "lsn": 5,
         "raw_log_page": 12, "raw_log_page_byte_offset": 49152,
         "transaction_id": 7},
    ]
    out = preimage.classify_expected(records, ["e34.bin", "other.bin"],
                                     815, 10, 0)
    by_name = {s["filename"]: s for s in out}
    check("a found record is positively identified",
          by_name["e34.bin"]["state"] == preimage.STATE_IDENTIFIED)
    check("a missing record in a parsed log is overwritten",
          by_name["other.bin"]["state"] == preimage.STATE_OVERWRITTEN)
    check("the found record keeps its LSN and page",
          by_name["e34.bin"]["lsn"] == 5 and by_name["e34.bin"]["rcrd_page"] == 12)

    out2 = preimage.classify_expected([], ["gone.bin"], 815, 3, 4)
    check("an undecodable log yields the undecodable state",
          out2[0]["state"] == preimage.STATE_UNDECODABLE)

    out3 = preimage.classify_expected([], ["gone.bin"], 815, 0, 0)
    check("an empty log yields the absent state",
          out3[0]["state"] == preimage.STATE_ABSENT)


# ------------------------------------------------------------ classification
def _info(**kw):
    base = {"arm": "C", "op": "create", "name": "e34.bin",
            "final_name": "e34.bin", "dir_record": 815,
            "op_status": "ok", "op_err": 0,
            "flush_status": "win32-dir", "flush_ok": True,
            "api_returned_success": True, "payload": 4096}
    base.update(kw)
    return base


def test_classification():
    print("R0..RX classification")
    pre_ok = {"states": [{"filename": "e34.bin",
                          "state": preimage.STATE_IDENTIFIED}]}

    v = recover.classify(_info(), pre_ok,
                         {"dir_readable": True, "expected_present": True},
                         True)
    check("R4 when the log evidence is there", v["class"] == "R4", v)

    v = recover.classify(_info(), {"states": [{"filename": "e34.bin",
                                              "state": "overwritten"}]},
                         {"dir_readable": True, "expected_present": True},
                         True)
    check("R3 when the log evidence is missing", v["class"] == "R3", v)

    v = recover.classify(_info(), None,
                         {"dir_readable": True, "expected_present": True},
                         True)
    check("R3 without a pre-recovery decode", v["class"] == "R3", v)

    v = recover.classify(_info(), pre_ok,
                         {"dir_readable": True, "expected_present": False},
                         True)
    check("R1 when a confirmed create is lost", v["class"] == "R1", v)

    v = recover.classify(_info(), pre_ok, {"dir_readable": False}, True)
    check("R0 when the directory cannot be listed", v["class"] == "R0", v)

    v = recover.classify(_info(), pre_ok,
                         {"dir_readable": True, "expected_present": True},
                         False)
    check("RX when the trial is invalid", v["class"] == "RX", v)

    # rename: both names present is an inconsistency, not a loss
    v = recover.classify(
        _info(op="rename", final_name="e34.bin.ren"),
        pre_ok,
        {"dir_readable": True, "expected_present": True,
         "expected_absent": False, "both_present": True,
         "neither_present": False},
        True)
    check("R1 when a rename left both names", v["class"] == "R1", v)

    v = recover.classify(
        _info(op="rename", final_name="e34.bin.ren"),
        pre_ok,
        {"dir_readable": True, "expected_present": False,
         "expected_absent": True, "both_present": False,
         "neither_present": True},
        True)
    check("R1 when a rename lost both names", v["class"] == "R1", v)

    v = recover.classify(
        _info(op="rename", final_name="e34.bin.ren"),
        pre_ok,
        {"dir_readable": True, "expected_present": True,
         "expected_absent": True, "both_present": False,
         "neither_present": False},
        True)
    check("R3 for a clean rename recovery", v["class"] == "R3", v)

    # delete: the name must be gone
    v = recover.classify(
        _info(op="delete"), pre_ok,
        {"dir_readable": True, "expected_present": False,
         "expected_absent": True},
        True)
    # The pre-recovery log positively identifies the delete operation, so the
    # trial carries independent evidence: R4, not R3.
    check("R4 for a clean delete recovery with log evidence",
          v["class"] == "R4", v)
    v = recover.classify(
        _info(op="delete"), {"states": [{"filename": "e34.bin",
                                        "state": "overwritten"}]},
        {"dir_readable": True, "expected_present": False,
         "expected_absent": True},
        True)
    check("R3 for a clean delete recovery without log evidence",
          v["class"] == "R3", v)

    v = recover.classify(
        _info(op="delete"), pre_ok,
        {"dir_readable": True, "expected_present": True},
        True)
    check("R1 when a deleted name came back", v["class"] == "R1", v)

    # a failed candidate API means only the pre-crash state is expected
    v = recover.classify(
        _info(api_returned_success=False, flush_ok=False), pre_ok,
        {"dir_readable": True, "expected_present": False}, True)
    check("a failed flush is not counted as a lost operation",
          v["class"] == "R3", v)


# ------------------------------------------------------------- statistics
def test_statistics():
    print("statistical bound")
    check("n=30 gives about a 10% bound",
          abs(aggregate.upper_bound(30) - 0.0952) < 0.001,
          aggregate.upper_bound(30))
    check("n=100 gives about a 3% bound",
          abs(aggregate.upper_bound(100) - 0.0296) < 0.001,
          aggregate.upper_bound(100))
    check("n=300 gives about a 1% bound",
          abs(aggregate.upper_bound(300) - 0.00995) < 0.001,
          aggregate.upper_bound(300))
    check("n=0 has no bound", aggregate.upper_bound(0) is None)


def test_aggregate():
    print("aggregation")
    trials = []
    for i in range(30):
        trials.append({
            "trial": {"arm": "C", "op": "create", "dir": "D:\\e34"},
            "verdict": {"class": "R4" if i < 28 else "R3"},
            "preimage": {"states": [{"filename": "e34.bin",
                                     "state": "positively identified"}]},
        })
    trials.append({
        "trial": {"arm": "A", "op": "create", "dir": "D:\\e34"},
        "verdict": {"class": "RX"},
        "preimage": {},
    })
    summary = aggregate.summarize(trials)
    c = summary["C/create"]
    check("arm totals are grouped", c["trials"] == 30 and c["valid_trials"] == 30)
    check("R4 count is right", c["classes"]["R4"] == 28)
    check("the bound is reported when there are no failures",
          c["failure_probability_upper_bound_95"] is not None)
    a = summary["A/create"]
    check("an invalid arm has no bound",
          a["failure_probability_upper_bound_95"] is None)


def main() -> int:
    print("E34 self-tests")
    test_controller_manifest()
    test_controller_native_status()
    test_controller_clock_offset()
    test_controller_kill_pid_file()
    test_preimage_states()
    test_classification()
    test_statistics()
    test_aggregate()
    print("")
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all E34 self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
