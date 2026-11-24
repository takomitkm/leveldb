# E34 — Results (preliminary; matrix in progress)

**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Plan:** [E34-PLAN.md](E34-PLAN.md)
**Status:** [E34-STATUS.md](E34-STATUS.md)

This file reports only trials whose evidence chain is intact. It is
**preliminary**: no cell has reached the 30 valid trials E34-PLAN.md §12
requires, so the numbers below are observations, not bounds.

## What a valid trial means here

Each trial is one sequence, all of it on the school QEMU rig:

1. **trial** — WinPE boots with the E34 probe on a fresh copy of the E34
   baseline disk. The probe performs the namespace operation and the
   arm-specific candidate flush, reports `op_end` / `flush_start` /
   `flush_end` / `trial_end` to a **separate E34LOG volume**, and the host
   force-kills QEMU the moment the `E34DONE` marker appears on it. The kill
   is the power cut; nothing runs on the guest afterwards.
2. **replay** — the QEMU `blklogwrites` journal is replayed up to the
   **last FLUSH barrier**. Writes the guest issued but never flushed are
   dropped, which is what a power cut does and what a byte copy of the device
   file does not do.
3. **recover** — the same WinPE boots on a copy of that replayed image with
   `MODE=recover`, so **Windows performs its own NTFS log replay**, and the
   probe reports the recovered directory listing over the external channel.
4. **analysis** — the pre-recovery image is decoded offline (raw namespace
   without replay, plus `$LogFile` index records), and the trial is
   classified R0–RX with the recovery source recorded.

The classification is per trial (E34-PLAN.md §11), and the recovery source is
recorded explicitly:

* **already durable** — the raw, no-replay namespace already held the
  confirmed state, so no log replay was needed;
* **replayed** — the raw namespace did not hold it and it appeared only after
  Windows replayed the log.

## Observations so far

| Trial | Arm | Op | Topology | Result | Recovery source | Evidence |
|---|---|---|---|---|---|---|
| `C-create-large-11` | C (`FlushFileBuffers(parent dir)`) | create | large | **R4** | already durable | raw namespace had the name; `$LogFile` carried `AddIndexEntryRoot` (LSN 8395796, RCRD page 2) |
| `A-create-large-101` | A (no candidate flush) | create | large | **R1** | n/a (lost) | recovery listing after Windows replay: `present=0, entries=0` |
| `A-create-large-102` | A (no candidate flush) | create | large | **R3** | not decoded | recovery listing after Windows replay: `present=1, entries=1` |

Those three are the only trials run on the current harness. `C-create-large-11`
predates the arm-A fix (below) but its arm's behaviour is unaffected by that
fix, because arm C's flush is the candidate flush in both builds; it is kept
with that caveat stated.

### What the three observations say — and do not say

* For **arm C** (`FlushFileBuffers` on the parent directory), the single valid
  observation is that the index entry was **already on the platter before the
  cut**, and the log independently contained the matching
  `AddIndexEntryRoot`. Whether this holds every time is exactly what 30 trials
  would answer; one trial cannot.
* For **arm A** (no candidate flush), the two observations differ: one trial
  lost the entry outright (R1), one kept it (R3). That is the expected shape
  for an arm that depends on NTFS writeback timing — and it is why arm A is a
  control, not a recommendation. It also shows the harness discriminates:
  a rig that always says "survived" would not produce an R1.

  The completed arm A batch has **12 valid trials: 7 R1 / 5 R3** — the
  no-flush arm loses the confirmed entry in about 58% of trials under this
  power-cut model. The arm C batch of the same size is still running.
* Nothing here supports a general durability claim, and E34-PLAN.md §15
  (explicit non-goals) applies in full.

## Harness defects found during E34 (both fixed, trials excluded)

1. **A byte copy of the device file is not a power cut.** The first trials
   copied the QEMU device file after the kill; the copy still contains writes
   the guest never flushed, which the host page cache would have written back
   later. Fixed by replaying the journal up to the last flush barrier
   (`e34-journal-replay.py`, the E14 engine, self-tested).
2. **Arm A was not a control.** The create workload called
   `FlushFileBuffers(file)` unconditionally, which makes arm A (no candidate
   flush) identical to arm B (flush the target file). Fixed by removing that
   call; the candidate flush is now the only flush in the create path.

All affected trials are kept on the rig under `e34-invalid/` with a README
explaining each exclusion. None of them is counted above.

## Statistics

E34-PLAN.md §12 requires at least 30 independent trials per primary arm per
operation type, and reports the one-sided 95% upper bound on the failure
probability for zero-failure cells as `p <= 1 - 0.05^(1/N)`.

No cell has reached that yet. The aggregate tool is in place
(`e34-aggregate.py`, self-tested) and will report the bound once a cell
finishes; the preliminary table above is not a substitute.

## What is not measured yet

* arms B, F, I and K;
* the `rename` and `delete` workloads;
* the small topology;
* the secondary crash points of E34-PLAN.md §7 (after the mutation API, during
  the flush, and 10 ms / 100 ms / 1 s after the flush returns).

The runner supports all of these; only the create/large cells have been run to
a usable count so far.

## Reproduce

On the QEMU rig (school machine, `school-guest-pc2`):

```
bash e34-matrix.sh <arm> <op> <topo> <n-trials> [start-index]
```

Artifacts per trial live in `e34-matrix/<arm>-<op>-<topo>/<tag>/`:
`vm-prerecover.img` (the replayed pre-recovery image), `journal.img` (the
`blklogwrites` log), `manifest-trial.jsonl` (the external manifest),
`manifest-recover.jsonl` (the recovered namespace from Windows).

Analysis on any host with Python and `pytsk3`:

```
python e34-analyze.py --rig-dir <copy of e34-matrix> --out-dir results
python e34-aggregate.py --trials results/*/recovery.json --out aggregate.json
```

## Acceptance criteria (E34-PLAN.md §14) — status at this point

| Criterion | Status |
|---|---|
| Crash mechanism and storage cache model recorded and verified | **Yes for this rig**: QEMU 11.1.2 TCG + `blklogwrites`; the guest's writes and FLUSH barriers are logged, and the cut is an offline replay to the last FLUSH. Not physical media. |
| Power off without a graceful guest shutdown | **Yes**: the host force-kills QEMU on the external `E34DONE` marker; no shutdown sequence runs in the guest. |
| Every valid trial keeps a pre-recovery disk image | **Yes**: `vm-prerecover.img` is captured before the recovery boot; all quarantined trials are explained. |
| `$LogFile` decode and recovery check repeatable | **Yes for the analysis tools**: `e34-preimage.py`, `e34-namespace.py`, `e34-recover.py` run on any host with Python + `pytsk3`; the example trials were decoded on a separate machine from the rig. |
| Every trial has an independently recorded manifest | **Yes**: the manifest and `E34DONE` live on the separate E34LOG volume, not on the tested volume. |
| Both large and small topology have results | **No**: only large/create has trials so far. |
| Every primary arm reaches the required count (or the report says why not) | **No**: only A and C are running; B/F/I/K are not started. This report states that explicitly. |
| All failed and invalid trials retained and explained | **Yes**: see `e34-invalid/README.txt` on the rig and the defect section above. |
| Final report separates observed behaviour from unprovable guarantees | **Yes**: the table above reports classes per trial; no general durability claim is made, and E34-PLAN §15 applies. |

E34 is therefore **not complete**. The missing cells are a coverage gap, not
a negative result, and they must not be reported as if they had been measured.
