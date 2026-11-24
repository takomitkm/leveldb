# E34 — Status: create/large matrix done, B/F running

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

* **arm C** (`FlushFileBuffers(parent directory)`), 10 valid trials: **10 R4,
  0 lost**. In 7 of the 10 the raw namespace was **empty** and the entry came
  back only through Windows' log replay; the other 3 already had it on the
  platter.
* **arm B** (`FlushFileBuffers(target file)`): **10/10 R4**; 8 of the 10 had an
  empty raw namespace and came back only through replay.
* **arm F** (`FlushFileBuffers($I30:$INDEX_ALLOCATION stream)`): **10/10 R4**,
  log record present in all 10; raw-namespace classification still running.
* **arm A** (no candidate flush), 12 valid trials: **7 R1 (lost)**, 5 R4. All
  5 survivors already had the entry on the platter; no arm A trial was rescued
  by replay.
* That gives the mechanism: the parent-directory flush does not write the
  index page (consistent with E17/E21 and with E33.1's "L3 but no L4"), but it
  makes the `$LogFile` `AddIndexEntryRoot` record durable, and NTFS's own
  replay rebuilds the entry after the power cut.

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

## What is running / missing

* **not run**: arm I, the arm K control, the `rename`/`delete` workloads, the
  small topology, and the secondary crash points of E34-PLAN §7.

## What this does NOT establish

* a useful statistical bound: 10–12 trials per cell are far below the 30 that
  §12 requires, so the only number is `p <= 25.9%` for the zero-failure B/C/F
  cells — too weak to call a durability guarantee;
* anything about physical media: the storage model is QEMU `blklogwrites` with
  an offline last-FLUSH cut, and E34-PLAN §15 applies in full;
* any general "NTFS is durable if you flush X" rule: the result is scoped to
  the arm, operation and topology measured;
* arm K remains a control only; the E26 withdrawal stands.

## CI coverage (run 45323673899)

The Actions run validates the analysis chain, not the crash: the probe builds
with MSVC, the self-tests pass (manifest reduction, clock alignment, four
pre-recovery states, every R0–RX branch, the statistical bound), a live
external-channel rehearsal records all eight events, and the E33 decoders pass
their self-tests. The power-cut evidence comes only from the rig.