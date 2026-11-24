# E34 — Status: crash-recovery pipeline runs end-to-end; matrix in progress

**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Plan:** [E34-PLAN.md](E34-PLAN.md)
**Results:** [E34-RESULTS.md](E34-RESULTS.md)
**CI run:** [45323673899](https://github.com/takomitkm/leveldb/actions/runs/45323673899) (success)

## What changed since the bootstrap trial

The bootstrap trial ([E34-BOOTSTRAP.md](E34-BOOTSTRAP.md)) validated the crash
harness but could not be part of the ledger: its manifest lived on the tested
volume. That blocker and two further harness defects are now fixed, and the
full trial → crash → pre-recovery capture → Windows log replay → classification
chain runs on the school QEMU rig.

| Piece | State |
|---|---|
| WIM injection | solved: `wimlib-imagex` 1.14.4 (official Windows build) rewrites `/e13pe.exe` in both image indexes; the ISO is rebuilt with the rig's original `genisoimage` recipe inside the CI guest |
| External manifest | the probe writes its manifest and the `E34DONE` marker to a **separate E34LOG volume** (a second raw disk), never to the tested volume; the host polls that volume image and cuts the power |
| Power-cut model | the QEMU `blklogwrites` journal is replayed **up to the last FLUSH barrier** (`e34-journal-replay.py`, the E14 engine); copying the raw device file would keep unflushed host-cache writes and would not be a power cut |
| Recovery | the same WinPE boots on a copy of the replayed image with `MODE=recover`, so **Windows performs its own NTFS log replay**, and reports the recovered namespace over the external channel |
| Analysis | pre-recovery `$LogFile` decode + raw (no-replay) namespace dump + R0–RX classification with an explicit `recovery_source` (already durable vs recovered by replay) |

## First valid trials

`C-create-large-11` is the first trial that ran on the corrected power-cut
model (the harness defect below was fixed in a later ISO):

```
phase 1  E34DONE at 70 s, QEMU force-killed (power cut)
replay   total=132 records, selected=122 (LAST flush cut), 8.15 MB applied
phase 3  E34DONE at 65 s: recover_result ok=true present=1 entries=1 size=4096
verdict  R4  ("the confirmed state was already on the platter" +
               "the index operation was found in the pre-recovery $LogFile")
```

That is an R4 observation: arm C's parent-directory flush put the index entry
on the platter before the cut, and the pre-recovery `$LogFile` independently
carries `AddIndexEntryRoot` for the same name (LSN 8395796, RCRD page 2).

## Harness defects found and fixed (trials quarantined)

1. **Raw device copy is not a power cut.** The first trials copied the QEMU
   device file after the kill; the copy still contains writes the guest never
   flushed. Fixed by the journal replay cut.
2. **Arm A was not a control.** The create workload called
   `FlushFileBuffers(file)` unconditionally, which makes arm A (no candidate
   flush) identical to arm B (flush the target file). Fixed by removing that
   call; the candidate flush is now the only flush in the create path.

Every affected trial is kept in the rig's `e34-invalid/` directory with a
README explaining why, and none of them is counted. The A and C trials above
were run before the second fix, so they are also excluded; the current harness
is `pe-e34N.iso` with `e34-matrix.sh` using journal replay.

## What is running / missing

* **done**: arm A (no candidate flush) 12 valid trials and arm C
  (`FlushFileBuffers(parent directory)`) 10 valid trials, both `create` on the
  large topology, each with the external manifest, a kept pre-recovery image,
  the `$LogFile` decode and the raw-namespace dump;
* **running**: arm B (`FlushFileBuffers(target file)`) and arm F
  (`FlushFileBuffers($I30 stream)`) batches of 10, same cell;
* **not run**: arm I and the arm K control, the `rename`/`delete` workloads,
  the small topology, and the secondary crash points.

`E34-RESULTS.md` records exactly which cells exist, the per-trial classes and
the recovery route; it does not merge arms and does not claim a general
durability result.

## What this does NOT establish

* no arm has reached 30 valid trials yet, so no cell has a usable bound;
* the storage model is QEMU `blklogwrites` with an offline last-flush cut, not
  physical media — E34-PLAN section 15 still applies in full;
* a single R4 says nothing about how often it holds;
* arm K remains a control only; the E26 withdrawal stands.

## CI coverage (run 45323673899)

The Actions run validates the analysis chain, not the crash: the probe builds
with MSVC, the self-tests pass (manifest reduction, clock alignment, four
pre-recovery states, every R0–RX branch, the statistical bound), a live
external-channel rehearsal records all eight events, and the E33 decoders pass
their self-tests. The power-cut evidence comes only from the rig.
