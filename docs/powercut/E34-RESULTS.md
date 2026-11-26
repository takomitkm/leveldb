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

| Arm | Candidate flush | Op | Topology | Trials (valid) |
|---|---|---|---|---|
| **A** | none (control) | create | large | 30 (30) |
| **B** | `FlushFileBuffers(target file)` | create | large | 30 (29) |
| **C** | `FlushFileBuffers(parent directory)` | create | large | 30 (30) |
| **F** | `FlushFileBuffers($I30:$INDEX_ALLOCATION stream)` | create | large | 30 (30) |

Classes (E34-PLAN.md §11), per trial, no merging:

| Arm | R1 (confirmed op lost) | R3 | R4 (correct namespace + log evidence) | RX | Total | Zero-failure bound |
|---|---|---|---|---|---|---|
| **A** | **9** (30%) | 0 | 21 | 0 | 30 | n/a (9 failures) |
| **B** | 0 | 0 | **29** | 1 | 30 | `p <= 9.8%` |
| **C** | 0 | 0 | **30** | 0 | 30 | `p <= 9.5%` |
| **F** | 0 | 0 | **30** | 0 | 30 | `p <= 9.5%` |

The one B `RX` is a recovery boot that died before writing a report — an
evidence gap, not a failed mount (the classifier now separates those: a
missing report is RX, a reported failed mount is R0).

Every trial in both arms had an intact evidence chain: external manifest on
the E34LOG volume, pre-recovery image kept (SHA-256 recorded on the rig),
`$LogFile` decode, and a recovery listing produced by Windows after its own
replay.

## What the two arms actually did

The interesting part is not only whether the entry survived, but *how*:

| Arm | Survived | Already on the platter at cut | Restored by replay | Lost |
|---|---|---|---|---|
| **A** | 21 / 30 | 21 | 0 | 9 / 30 |
| **B** | 29 / 29 | 13 | **16** | 0 |
| **C** | 30 / 30 | 23 | **7** | 0 |
| **F** | 30 / 30 | 15 | **15** | 0 |

The raw namespace was checked on **every** pre-recovery image (120 of 120,
`dump-namespaces.py`), so this table is complete, not a sample.

**Do not read the last two columns as an arm effect.** The crash delay is
uncontrolled (next section), and it is exactly the window in which NTFS's
lazy writer can put the entry on the platter. The split shifts by **batch**,
not by arm:

| Arm | first batch (already durable / replayed) | second batch |
|---|---|---|
| A | 5 / 0 (of 12) | 16 / 0 (of 18) |
| B | 2 / 8 (of 10) | 11 / 8 (of 20, +1 RX) |
| C | 3 / 7 (of 10) | **20 / 0** (of 20) |
| F | 4 / 6 (of 10) | 11 / 9 (of 20) |

Arm C is the clearest case: its first batch needed replay in 7 of 10 trials,
its second batch in 0 of 20 — same ISO, same probe, same harness. That is a
batch-level drift in the crash timing, and it is larger than any cross-arm
difference here. So the cross-arm split (e.g. C 23/7 vs B 13/16, Fisher
p = 0.017) is **not** attributable to the flush.

What the column does support is within-arm and qualitative: in 16 of 29 arm B
trials and 15 of 30 arm F trials the index page was demonstrably **not** on
the platter at cut time, i.e. those flushes did not write it.

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

One nuance matters for how strong a claim this supports: the **five arm A
survivors also had a `positively identified` log record**, even though no
flush was issued — writeback had committed it by luck. So the flush does not
*make replay possible*; it *guarantees at API return* that the record the
replay needs is already durable. Without the flush that is a race, and 7 of
12 trials lost it.

That distinction is exactly the E34 question, and it is not visible from a
directory listing alone; it required the raw-namespace dump on the
pre-recovery image. It also lines up with E33.1: the parent-directory flush
reached the log pages with a barrier (L3) but never wrote the `$I30` home page
(no L4) — E34 shows what that combination is worth after a power cut.

## What this does and does not establish

**Established for this cell (arm/op/topology as stated, this rig):**

* Across **89 valid flush-arm trials** (B 29, C 30, F 30) the confirmed
  create was present after the modelled power cut **89/89**, and the
  pre-recovery `$LogFile` independently carried the matching
  `AddIndexEntryRoot` record in **all 89**.
* The three flush shapes are indistinguishable: B 29/29, C 30/30, F 30/30,
  and the B-vs-F replay split is p = 0.796.
* The no-flush control lost the same confirmed create in **9/30** trials, so
  the harness discriminates and the result is not "everything always
  survives" — but see the crash-point caveat below: that 9/30 is not a stable
  rate.
* The recovery route for most flush-arm trials was log replay, not a direct
  index write: the raw namespace was empty in 15 of 30 arm F trials, 16 of 29
  arm B trials and (see the per-trial CSV) a similar share of arm C.

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

## B vs F: flushing the file and flushing the `$I30` stream are equivalent here

At 30 trials each the two arms are indistinguishable: B 29/29 R4 (plus one
evidence-gap RX), F 30/30 R4; and their replay/durable split is 16/13 vs
15/15, Fisher exact two-sided **p = 0.796**.

That is expected once the mechanism is right: the `$I30` handle points at the
directory's index-allocation stream, which is the object that *could* carry
the index page, but at flush time the new entry has not reached that stream's
dirty data yet (it lives in the in-memory index buffer and in the log). So F
flushes nothing relevant on the index side either, and what both arms really
commit is the volume's **shared `$LogFile`**.

## The crash point is "within a few seconds", not "immediate"

The host detects the guest's completion by polling the E34LOG volume image in a
loop with a 5 s sleep (plus one liveness check), so the power cut lands
**roughly 0-6 s after the guest's `trial_end`**, not at the instant the
candidate API returned. E34-PLAN.md §7 asks for the primary crash point to be
immediately after the return.

This does not invalidate the flush-arm result, but it does bound what it
means: a flush that survives a cut 0-6 s later is weaker evidence than one
that survives an immediate cut, because NTFS's lazy writer may have run in
that window. It matters more for the no-flush arm, where the same window lets
writeback commit the very records the flush is supposed to guarantee.

The arm A split shows this is not academic: its first 12 trials lost **7**
(58%) and its next 18 lost **2** (11%) — Fisher exact two-sided p = 0.0125, so
the two batches are not drawn from one stable rate. The most plausible driver
is exactly this delay window versus the lazy-writer tick, but the data here
cannot separate that from other environment drift, so **no arm A loss rate
should be quoted as a property of the no-flush shape** until the crash point
is tightened (poll the marker at sub-second intervals and record the measured
delay per trial).

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

`e34/results/aggregate-20261126.json` and `.csv` are the per-arm aggregate
produced by `e34-aggregate.py` over all 120 trials; `e34/results/trials-20261126.csv`
is the per-trial table (class, recovery source, flush status, and whether the
raw namespace already held the name). Both are generated from the trial JSONs,
not hand-written. The earlier `*-20261124` files were the 42-trial subset and
are superseded by these.

## Reproduce

On the QEMU rig (school machine, reached through `school-guest-pc2`), with the
WinPE ISO built by the genisoimage recipe (`docs/powercut/e17_gbld.sh` lineage)
and the two volumes `e34-base.img` / `e34log.img` in place:

```
bash e34-matrix2.sh <arm> <op> <topo> <n-trials> <start-index>
#   per trial: probe trial -> blklogwrites journal replayed to the LAST FLUSH
#   (docs/powercut/e14_analyze.py replay <journal> <disk> <cut> LAST)
#   -> Windows recovery boot (MODE=recover) -> in-place finalise: preimage
#   decode, gzip the pre-recovery image and the journal, drop the raw files
bash e34-stage.sh <arm>-<op>-<topo>
#   copies each compressed pre-recovery image into the SSH home as
#   e34-pre-<cell>-<tag>.gz, appends its SHA-256, tars the small artifacts
bash e34-analyze-rig.sh
#   pre-recovery $LogFile decode for every trial with a raw image left
```

`e34-matrix.sh` is the earlier version of the runner (kept for the first
batches: it leaves the raw images in place and relies on `e34-finalize.sh`).
The journal-replay engine is `docs/powercut/e14_analyze.py` — `replay ... LAST`
cuts the disk at the last FLUSH barrier; there is no separate copy in `e34/`.

Analysis elsewhere (Python + `pytsk3`):

```
python dump-namespaces.py --list <staged list> --work-dir <dir> --out ns.jsonl
python e34-collect.py --work-dir <per-trial dirs> --namespaces ns.jsonl     --out-dir results
python e34-aggregate.py --trials "results/*.json" --out aggregate.json --csv aggregate.csv
```

