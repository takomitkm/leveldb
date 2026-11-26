# E34 / E34.1 — Status: create/large matrix done (4 x 30); immediate-trigger chain proved on one trial

**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Plan:** [E34-PLAN.md](E34-PLAN.md)
**Results:** [E34-RESULTS.md](E34-RESULTS.md)
**CI run:** [45323673899](https://github.com/takomitkm/leveldb/actions/runs/45323673899) (success)

## Where the work stands

The full chain runs on the school QEMU rig and has produced its first usable
matrix cell (`create`, large topology):

| Piece | State |
|---|---|
| WIM injection | solved: `wimlib-imagex` 1.14.4 (official Windows build) replaces `/e13pe.exe` in both image indexes; the ISO is rebuilt with the rig's original `genisoimage` recipe inside the CI guest |
| External manifest | the probe writes its manifest and the `E34DONE` marker to a **separate E34LOG volume** (a second raw disk), never to the tested volume; the host polls that volume image and cuts the power |
| Power-cut model | the QEMU `blklogwrites` journal is replayed **up to the last FLUSH barrier**; writes the guest never flushed are dropped |
| Recovery | the same WinPE boots on a copy of the replayed image with `MODE=recover`, so **Windows performs its own NTFS log replay**, and reports the recovered namespace over the external channel |
| Analysis | pre-recovery `$LogFile` decode + **raw (no-replay) namespace dump on every image** + R0–RX classification with an explicit recovery source |

## Result so far (see E34-RESULTS.md for the tables)

Four cells of 30 trials each (`create` / large topology), 120 trials total:

| Arm | Candidate flush | Trials (valid) | Classes |
|---|---|---|---|
| A | none (control) | 30 (30) | **9 R1 lost**, 21 R4 |
| B | `FlushFileBuffers(target file)` | 30 (29) | 29 R4, 1 RX (recovery boot died) |
| C | `FlushFileBuffers(parent directory)` | 30 (30) | **30 R4** |
| F | `FlushFileBuffers($I30:$INDEX_ALLOCATION stream)` | 30 (30) | **30 R4** |

Zero-failure bound: `p <= 9.5%` for C and F, `p <= 9.8%` for B.

* The three flush shapes are **indistinguishable** (B 29/29, C 30/30, F 30/30;
  the B-vs-F replay/durable split gives Fisher p = 0.796). What they commit is
  the volume's shared `$LogFile`, not the index page: in roughly half the
  flush trials the raw namespace was still empty and Windows' replay produced
  the name.
* The no-flush arm loses entries, but its measured rate is **not stable**:
  7/12 in the first batch vs 2/18 in the second (Fisher p = 0.0125). The most
  plausible driver is the crash-point window below.

### Crash-point precision caveat

The host polls the E34LOG volume with a 5 s sleep plus a liveness check, so
the power cut lands roughly **0-6 s after the guest's `trial_end`**, not
immediately after the candidate API returned. Flush-arm results are therefore
"durable through a cut up to ~6 s later", which is weaker than "durable
immediately"; and the no-flush arm's loss rate is sensitive to that window
because the lazy writer can run inside it. Tightening the crash point is the
next harness change, not a reinterpretation of the data.

## Harness defects found and fixed (trials quarantined)

1. **Raw device copy is not a power cut.** The earliest trials copied the QEMU
   device file after the kill; the copy still contains writes the guest never
   flushed. Fixed by the journal last-FLUSH replay.
2. **Arm A was not a control.** The create workload called
   `FlushFileBuffers(file)` unconditionally, which made arm A (no candidate
   flush) identical to arm B (flush the target file). Fixed by removing that
   call; the candidate flush is now the only flush in the create path.

Both defects are documented in the rig's `e34-invalid/README.txt`, and none of
the affected trials is counted. The A and C batches reported above were run on
the fixed harness (`pe-e34N.iso`).

## What is done / what is next

* **done**: all four `create`/large cells at 30 trials each (A 30, B 30, C 30,
  F 30 = 120), each with the external manifest, a kept pre-recovery image plus
  its SHA-256, the `$LogFile` decode and the raw-namespace dump;
* **E34.1 (chain proved, 1 trial)**: the guest writes a short `E34CUT` token
  immediately after the candidate flush returns, to a pre-allocated file at a
  fixed offset in the E34LOG image; the host watches that offset unbuffered
  and force-kills QEMU. The first chain used a PowerShell `Stop-Process` with a ~2.3 s cold start
  (trials quarantined in `e34-invalid/e34.2-slowkill/`); the replacement watcher
  calls `TerminateProcess` directly (falling back to `taskkill -F -PID`). First
  receipt with the final code: detect -> kill request **3.2 microseconds**,
  kill request -> process exit **301 ms**, mechanism `terminateprocess`.
  Scripts: `e34/e34-trigger-watch.py`, `e34/e34-run-immediate.sh`;
* **not yet established**: that a flush-arm record is durable *at the instant
  of API return*. That needs the randomised A/B/C batch under this new
  trigger (next step), not one B trial;
* **then (E34.2)**: re-run A/B/C with the new trigger, randomised and
  interleaved rather than batched, 30 per arm before deciding on 100;
* **not run**: arm I, the arm K control, `rename`/`delete`, the small topology,
  and the secondary crash points of E34-PLAN §7;
* **after E34**: E35 runs the real LevelDB commit order (SST create + sync,
  MANIFEST append + sync, CURRENT rename) instead of a bare file create.

## What this does NOT establish

* a useful statistical bound beyond `p <= 9.5%` per zero-failure arm: reaching
  ~3% needs ~100 trials per arm, and none of these bounds is a durability
  guarantee;
* anything about physical media: the storage model is QEMU `blklogwrites` with
  an offline last-FLUSH cut, and E34-PLAN §15 applies in full;
* any general "NTFS is durable if you flush X" rule: the result is scoped to
  the arm, operation and topology measured;
* arm K remains a control only; the E26 withdrawal stands;
* **durability at the instant of API return** — the 0-6 s crash window is the
  specific gap E34.1 closes.

## CI coverage (run 45323673899)

The Actions run validates the analysis chain, not the crash: the probe builds
with MSVC, the self-tests pass (manifest reduction, clock alignment, four
pre-recovery states, every R0–RX branch, the statistical bound), a live
external-channel rehearsal records all eight events, and the E33 decoders pass
their self-tests. The power-cut evidence comes only from the rig.