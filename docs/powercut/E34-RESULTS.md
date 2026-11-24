# E34 — Results (first matrix complete for create/large; other cells still open)

**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Plan:** [E34-PLAN.md](E34-PLAN.md)
**Status:** [E34-STATUS.md](E34-STATUS.md)

This file reports only trials whose evidence chain is intact, and it keeps the
per-trial classes separate. It is **not** a general durability result: only the
`create` workload on the `large` topology has a usable batch, and that batch is
below the 30-trial target of E34-PLAN.md §12, so the numbers are observations,
not bounds.

## What a valid trial means here

Each trial is one sequence, all of it on the school QEMU rig:

1. **trial** — WinPE boots with the E34 probe on a fresh copy of the E34
   baseline disk. The probe performs the namespace operation and the
   arm-specific candidate flush, reports `op_end` / `flush_start` /
   `flush_end` / `trial_end` to a **separate E34LOG volume**, and the host
   force-kills QEMU the moment the `E34DONE` marker appears on it. The kill
   is the power cut; nothing runs in the guest afterwards.
2. **replay** — the QEMU `blklogwrites` journal is replayed up to the
   **last FLUSH barrier**. Writes the guest issued but never flushed are
   dropped. A byte copy of the device file would keep them (the host page
   cache writes them back later) and would not model a power cut at all.
3. **recover** — the same WinPE boots on a copy of that replayed image with
   `MODE=recover`, so **Windows performs its own NTFS log replay**, and the
   probe reports the recovered directory listing over the external channel.
4. **analysis** — the pre-recovery image is decoded offline twice: once for
   the `$LogFile` index records and once for the **raw, no-replay namespace**.
   The trial is then classified R0–RX, and how the namespace came to be
   correct is recorded separately (already on the platter vs restored by the
   replay).

## The batch

Both batches are arm/operation/topology cells of the same harness
(`pe-e34N.iso`, journal cut at the last FLUSH, external E34LOG manifest):

| Arm | Candidate flush | Op | Topology | Valid trials |
|---|---|---|---|---|
| **A** | none (control) | create | large | 12 |
| **B** | `FlushFileBuffers(target file)` | create | large | 10 |
| **C** | `FlushFileBuffers(parent directory)` | create | large | 10 |
| **F** | `FlushFileBuffers($I30:$INDEX_ALLOCATION stream)` | create | large | 10 |

Classes (E34-PLAN.md §11), per trial, no merging:

| Arm | R1 (confirmed op lost) | R3 | R4 (correct namespace + log evidence) | Total | Zero-failure bound |
|---|---|---|---|---|---|
| **A** | **7** | 0 | 5 | 12 | n/a (7 failures) |
| **B** | 0 | 0 | **10** | 10 | `p <= 25.9%` |
| **C** | 0 | 0 | **10** | 10 | `p <= 25.9%` |
| **F** | 0 | 0 | **10** | 10 | `p <= 25.9%` |

Every trial in both arms had an intact evidence chain: external manifest on
the E34LOG volume, pre-recovery image kept (SHA-256 recorded on the rig),
`$LogFile` decode, and a recovery listing produced by Windows after its own
replay.

## What the two arms actually did

The interesting part is not only whether the entry survived, but *how*:

| Arm | Survived | Survivors already on the platter | Survivors restored by replay | Lost |
|---|---|---|---|---|
| **A** | 5 / 12 | **5** (every survivor) | 0 | 7 / 12 |
| **B** | 10 / 10 | 2 | **8** | 0 / 10 |
| **C** | 10 / 10 | 3 | **7** | 0 / 10 |
| **F** | 10 / 10 | 4 | **6** | 0 / 10 |

The raw namespace was checked on **all 42** pre-recovery images (one image at
a time, `dump-namespaces.py`), so this table is complete, not a sample.

So the two arms reach the same "survived" state by different routes:

* **arm A is a coin flip.** 7 of 12 trials lost the confirmed create outright
  (`present=0, entries=0` after the recovery boot) and their raw namespace was
  empty too. Their pre-recovery decode reported the record as **overwritten**
  — the surviving log snapshot no longer carried it, which is not proof it was
  never written, only that the snapshot could not restore it. The 5 survivors
  all had the entry **already on the platter**; no arm A trial was rescued by
  replay. That is what a namespace change left to NTFS writeback timing looks
  like.
* **arm C never lost, and mostly by a different mechanism.** In 7 of the 10
  trials the raw namespace was **empty** — the parent-directory flush did not
  put the index entry on the platter — yet the name was there after Windows
  replayed the log. The other 3 already had it on the platter. So the flush's
  value is the **`$LogFile` barrier**, not an index-page write: it makes the
  `AddIndexEntryRoot` record durable, and NTFS's own replay reconstructs the
  entry.
* **arm B** (`FlushFileBuffers(target file)`) also never lost, 10/10, and its
  pre-recovery decode carries the same `AddIndexEntryRoot` record for every
  trial. Its raw namespace was empty in 8 of 10, so it too came back mostly
  through replay. That is the same mechanism: NTFS has one log per volume, so
  flushing the file commits the log entries the index operation had already
  written.
* **arm F** (`FlushFileBuffers` on the `$I30:$INDEX_ALLOCATION` stream, the
  handle E33.1 found needs `FILE_FLAG_BACKUP_SEMANTICS`): 10/10 restored, the
  pre-recovery decode carries the record for all 10, and 6 of 10 came back
  only through replay.

Taken together, the three flush arms are indistinguishable on this metric
under this crash model — what matters is that **some** flush commits the
shared log after the index operation; the no-flush arm is the one that loses,
and it loses only when writeback had not already reached the platter.

That distinction is exactly the E34 question, and it is not visible from a
directory listing alone; it required the raw-namespace dump on the
pre-recovery image. It also lines up with E33.1: the parent-directory flush
reached the log pages with a barrier (L3) but never wrote the `$I30` home page
(no L4) — E34 shows what that combination is worth after a power cut.

## What this does and does not establish

**Established for this cell (arm/op/topology as stated, this rig):**

* After a successful parent-directory flush followed immediately by the
  modelled power cut, NTFS log replay restored the confirmed create in
  **10/10** trials, and the pre-recovery `$LogFile` independently carried the
  matching `AddIndexEntryRoot` record in all 10.
* The same held for `FlushFileBuffers(target file)` and for the
  `$I30:$INDEX_ALLOCATION` stream flush: **10/10** each, log record present in
  all 10 — the three flush shapes are indistinguishable on this metric here.
* The no-flush control lost the same confirmed create in **7/12** trials, so
  the harness discriminates and the result is not "everything always
  survives".
* The recovery route for most flush-arm trials was log replay, not a direct
  index write: the raw namespace was empty in 7 of 10 arm C trials and 8 of 10
  arm B trials, and only the replay produced the name.

**Not established:**

* a useful bound: 10 and 12 trials are far below the 30 §12 requires. The only
  number available is `p <= 25.9%` for the zero-failure arm C cell, which is
  too weak to call a durability guarantee;
* arm I and the arm K control;
* the `rename` and `delete` workloads;
* the small topology;
* the secondary crash points of §7;
* anything about physical media: the storage model is QEMU `blklogwrites`
  with an offline last-FLUSH cut, and E34-PLAN §15 applies in full.

No arm is merged with another, and no claim is made that a specific flush API
"guarantees" durability. The supported statement is the one above, for this
cell and this crash model.

## Harness defects found during E34 (both fixed, trials excluded)

1. **A byte copy of the device file is not a power cut.** The first trials
   copied the QEMU device file after the kill; the copy still contains writes
   the guest never flushed. Fixed by replaying the journal up to the last
   flush barrier (`e34-journal-replay.py`, the E14 engine, self-tested).
2. **Arm A was not a control.** The create workload called
   `FlushFileBuffers(file)` unconditionally, which made arm A (no candidate
   flush) identical to arm B (flush the target file). Fixed by removing that
   call; the candidate flush is now the only flush in the create path.

All affected trials are kept on the rig under `e34-invalid/` with a README
explaining each exclusion. None of them is counted above.

## Acceptance criteria (E34-PLAN.md §14) — status

| Criterion | Status |
|---|---|
| Crash mechanism and storage cache model recorded and verified | **Yes for this rig**: QEMU 11.1.2 TCG + `blklogwrites`; the guest's writes and FLUSH barriers are logged and the cut is an offline replay to the last FLUSH. Not physical media. |
| Power off without a graceful guest shutdown | **Yes**: the host force-kills QEMU on the external `E34DONE` marker. |
| Every valid trial keeps a pre-recovery disk image | **Yes**: `vm-prerecover.img` per trial, plus a compressed copy and its SHA-256 on the rig host. |
| `$LogFile` decode and recovery check repeatable | **Yes**: `e34-preimage.py` / `e34-namespace.py` / `e34-recover.py` were run on a host other than the rig for the sample, and on the rig for all trials. |
| Every trial has an independently recorded manifest | **Yes**: manifest and `E34DONE` live on the separate E34LOG volume. |
| Both large and small topology have results | **No**: only large. |
| Every primary arm reaches the required count (or the report says why not) | **No**: A (12), B (10), C (10), F (10) are all below 30; arm I and the arm K control are not started. The counts are stated instead of hidden. |
| All failed and invalid trials retained and explained | **Yes**: `e34-invalid/README.txt` on the rig plus the defect section above. |
| Final report separates observed behaviour from unprovable guarantees | **Yes**: see the two sections above. |

E34 is therefore **not complete**. The missing cells are a coverage gap, not a
negative result, and they must not be reported as if they had been measured.

## Machine-readable record

`e34/results/aggregate-20261124.json` and `.csv` are the per-arm aggregate
produced by `e34-aggregate.py`; `e34/results/trials-20261124.csv` is the
per-trial table (class, recovery source, flush status, and whether the raw
namespace already held the name). Both are generated from the trial JSONs, not
hand-written.

## Reproduce

On the QEMU rig (school machine, `school-guest-pc2`):

```
bash e34-matrix.sh <arm> <op> <topo> <n-trials> [start-index]
bash e34-analyze-rig.sh          # pre-recovery $LogFile decode per trial
```

Analysis elsewhere (needs Python + `pytsk3`):

```
python dump-namespaces.py --list <list> --work-dir <dir> --out namespaces.jsonl
python e34-recover.py --manifest ... --preimage ... --recovery-report ... \
    --pre-namespace ...
python e34-aggregate.py --trials results/*.json --out aggregate.json
```