# E34 — NTFS directory-index crash consistency and `$LogFile` replay

**Status:** plan (results will land in `E34-RESULTS.md`)
**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Predecessor:** [E33.1-RESULTS.md](E33.1-RESULTS.md) (run 45297618762)
**Tier:** 2 — crash recovery

## 1. Objective

E33.1 established, per candidate API call, that the `$LogFile` RCRD pages holding a
test directory's `$I30` index operations can be written to the storage stack and
covered by a flush barrier that completes before the API returns (L3).

That is **not** a durability guarantee. E34 asks the next question:

> After a directory change and a **successful** candidate flush return, if the
> machine loses power immediately, does NTFS log replay restore the confirmed
> namespace change?

Five stages must be kept apart:

1. the index operation exists in `$LogFile`;
2. the corresponding log pages reached the storage stack;
3. the log data survived the power loss;
4. NTFS replayed the log;
5. the recovered `$I30` namespace reflects the confirmed operation.

**No stage may be inferred from another.** An ETW flush event is not evidence of
stable-media persistence; a successful mount is not evidence of a successful
replay; a single successful crash trial is not evidence of a general guarantee.

## 2. Required evidence per trial

Every trial (arm × operation × topology) must produce:

- the pre-recovery disk image (byte-identical copy);
- the pre-recovery `$LogFile` decode;
- the crash trigger timestamp and mechanism;
- the guest API call and return timestamps;
- the storage flush evidence (when obtainable);
- the post-recovery namespace listing;
- file identity and payload verification;
- the NTFS recovery / consistency-check output;
- reproducible single-trial metadata.

The final report must state explicitly whether the run observed:

- a confirmed-successful operation lost;
- a correct replay of a logged index operation;
- an inconsistent or corrupted directory index;
- differences between candidate flush handles;
- differences between resident and non-resident index topologies.

## 3. Test environment

A dedicated, disposable NTFS data volume. **The guest system volume is never
the primary test volume.**

Recorded: Windows version and build, NTFS version, cluster size, the virtual or
physical storage controller, the storage cache configuration, whether the
backend honours flush requests, whether a power cut discards the volatile write
cache, the hypervisor version, and the disk-image format and snapshot mechanism.

### 3.1 Power-cut validity

A guest OS crash is **not** automatically a physical power cut. Killing QEMU or
resetting a VM can still leave writes in the host page cache or in the storage
backend, i.e. data that a real power cut would have lost.

Before any VM trial is called a power-cut trial, the rig must show:

1. the storage backend has an explicit volatile write-cache model;
2. flush requests are propagated according to that model;
3. the crash mechanism actually discards un-persisted volatile writes;
4. terminating or resetting the guest does not silently flush pending writes
   during backend shutdown.

If (1)–(4) cannot be shown, the result is reported as **Guest Crash Recovery**
and never as physical power-cut persistence.

The rig uses the blklogwrites journal already used by E14/E17/E29–E31: it records
every write and every flush in order, so the post-crash volume state can be
reconstructed for an arbitrary cut point, and the *worst case* state (everything
after the last flush was lost) is the conservative power-cut model. The E14
analyzer's `replay … LAST` cut implements exactly that.

## 4. Topologies

Two directory layouts, reported as separate groups and never pooled.

**Large directory** — enough entries to make `$I30:$INDEX_ALLOCATION`
non-resident; the allocation stream's existence and size are recorded from the
`$MFT`, not inferred from a directory size. Target the E33.1 topology
(~512 KiB allocation stream, 2000 entries).

**Small directory** — the index stays resident in `$INDEX_ROOT`; the absence of a
non-resident `$INDEX_ALLOCATION` is confirmed from the `$MFT`.

## 5. Arms

The E33.1 handle/API set, unchanged:

| Arm | After the directory change |
|---|---|
| A | no candidate flush |
| B | `FlushFileBuffers(target file)` |
| C | `FlushFileBuffers(parent directory)` |
| F | `FlushFileBuffers($I30:$INDEX_ALLOCATION stream)` |
| I | `NtFlushBuffersFileEx($I30 stream, 8)` |
| K | `FlushFileBuffers(volume root)` — positive control only |

The `$I30` stream is opened exactly as in E33.1, including
`FILE_FLAG_BACKUP_SEMANTICS` where required.

Opening `$LogFile` as an ordinary file handle is **not** attempted as a
candidate: E33.1 showed that path is unavailable.

Optional secondary arms (parent-directory `NtFlushBuffersFile` and
`NtFlushBuffersFileEx(0)`) are reported separately from the primary groups.

Arm K is a control, never a production recommendation (the E26 withdrawal
stands).

## 6. Workload

Every trial uses a unique file name, and the target directory's MFT reference is
recorded. Create, rename and delete are tested **independently** — never merged
into one workload, because the recovery outcome is then impossible to classify.

### 6.1 Create

1. create a uniquely named file in the target directory;
2. write a known payload;
3. flush the file content consistently across arms;
4. close the file handle if the operation contract requires it;
5. call the candidate directory/index flush;
6. report the API result over the external channel;
7. trigger the configured crash point.

Expected after recovery when the flush returned success: the new name exists,
the file identity matches the trial manifest, and the payload is intact.

### 6.2 Rename

1. create and initialise the source file;
2. rename it to a unique target name;
3. record the rename API result;
4. call the candidate flush;
5. report the API result over the external channel;
6. trigger the configured crash point.

Expected after recovery: the target name exists, the source name does not, and
the file identity is unchanged. These must be recorded explicitly when they
occur: both names present; neither name present; inconsistent directory index.

### 6.3 Delete

1. create and initialise a uniquely named file;
2. close every handle that could defer the delete;
3. perform the delete;
4. record the API result;
5. call the candidate flush;
6. report the API result over the external channel;
7. trigger the configured crash point.

Expected after recovery: the deleted name is absent, the directory enumeration
is consistent, and no stale reference to the deleted entry survives.

## 7. Crash timing

The primary crash point is immediately after the candidate API returns.

The controller lives **outside** the guest, so the guest never writes a marker to
the test volume to announce the crash.

**Primary:** the guest completes the candidate flush, reports API success over
the external channel, and the controller performs the sudden power-off
immediately afterwards. The interval between the API return and the actual
power-off is measured, not assumed to be zero.

**Secondary points**, to separate behaviour around the flush boundary:

1. after the directory-change API returns, before the candidate flush starts;
2. during the candidate flush call;
3. immediately after the candidate flush returns;
4. ~10 ms after it returns;
5. ~100 ms or ~1 s after it returns.

A crash *during* the operation may legitimately leave either the old or the new
namespace state; the requirement there is only that the filesystem stays
consistent, with no partially written or corrupted index structure.

For every call that had already returned success before the crash, the
recovered state is compared against the externally recorded manifest.

## 8. External evidence channel

The trial manifest is **not** stored on the tested NTFS volume.

The channel is a host-side socket (the guest connects to the controller over
QEMU user-mode networking), with a serial-port fallback for the WinPE-style
rings. Recorded per trial:

trial id, arm id, directory topology, operation type, target and expected final
name, target directory MFT reference, mutation API entry and return timestamps,
candidate flush API entry and return timestamps, API status, controller receipt
timestamp, actual power-off timestamp, crash mechanism, and backend state.

The controller never requires the guest to perform a filesystem flush to record
a marker. The channel's own overhead is measured (round-trip time of a
timestamp echo). The controller keeps its own log, independent of the guest.

## 9. Pre-recovery capture

**Mandatory step.** After the power loss and *before* Windows is allowed to mount
and recover the volume, a byte-identical copy of the disk image is saved. The
forensic analysis, the NTFS recovery and any repeated recovery run on separate
copies.

On the pre-recovery forensic copy:

1. decode `$LogFile` with the E33 decoder;
2. identify the target index operations by file name and parent MFT reference;
3. record the RCRD pages that contain each matching operation;
4. compare the matching physical pages with the pre-crash writes observed by
   the storage trace, when available;
5. record whether the operation is present in the surviving log snapshot;
6. record the LSN, RCRD physical page, and the decoder's confidence.

`$LogFile` is circular, so a missing record after the crash does **not** mean
the record was never written. The decoder must distinguish:

- **overwritten** — the page exists but no longer carries the record;
- **absent** — the page never contained the record;
- **undecodable** — the page is present but cannot be parsed;
- **positively identified** — the record was found and attributed.

No mount or recovery is started before the forensic copy exists.

## 10. Recovery procedure

Using an independent copy of the pre-recovery image:

1. boot Windows or WinPE and mount the test volume;
2. let NTFS perform its normal log recovery;
3. do **not** run `chkdsk /f` before collecting the initial state;
4. record the recovery messages and the relevant event log;
5. record the volume's dirty state where available;
6. enumerate the target directory;
7. check the expected name, the source/target name state, the file identity and
   the payload hash;
8. record whether the directory enumeration and the file opens succeeded;
9. only after the initial state is collected, run a non-repairing consistency
   check such as `chkdsk /scan`;
10. save the post-recovery image and all logs.

Repair operations are never used to make a failed trial look successful. Any
repair runs on a separate copy and is reported separately.

## 11. Result classification

Each trial is classified on its own.

| Class | Meaning |
|---|---|
| R0 | the volume could not be mounted, or recovery failed |
| R1 | the volume mounted, but a confirmed-successful operation was lost |
| R2 | the namespace or directory index is inconsistent |
| R3 | the correct namespace was recovered after log replay |
| R4 | the namespace was recovered correctly **and** the corresponding index/log evidence was independently verified |
| RX | the trial is invalid (crash mechanism, storage-cache model or evidence chain) |

R3 observes a successful recovery. R4 additionally requires evidence beyond the
directory listing itself — evidence that links the pre-recovery log to the
post-recovery index state.

A volume that merely mounts is never classified as a successful trial.

## 12. Repetition and statistics

One crash trial cannot prove that a behaviour always holds.

**At least 30 independent trials per primary arm per operation type.** Each
trial uses a fresh baseline image, or a full restore to the baseline.

Promising arms are extended to 100 or more trials.

With N independent trials and zero failures, the one-sided 95% upper bound on
the failure probability is approximately

```
P_failure ≈ 3 / N
```

| trials with zero failures | 95% upper bound |
|---|---|
| 30 | ~10% |
| 100 | ~3% |
| 300 | ~1% |

These bounds apply only to the tested conditions and are not a general
guarantee. Every trial, including invalid ones, stays in the ledger, and an
excluded trial states why.

## 13. Data analysis

Reported per arm:

- valid trial count;
- R0 / R1 / R2 / R3 / R4 / RX counts;
- confirmed-successful operations that were ultimately lost;
- operations correctly recovered;
- `$LogFile` record detection rate;
- pre-crash RCRD page-write detection rate;
- flush evidence acquisition rate;
- post-recovery namespace correctness rate;
- resident vs non-resident index difference;
- power-off delay distribution;
- storage cache and backend configuration.

Every candidate handle is compared against the no-flush arm A.

A handle that merely produced more `$LogFile` writes is **not** better.

**The primary metric is whether the namespace is correctly recovered after the
power loss under the stated crash model.**

## 14. Acceptance criteria

E34 is complete when:

- the crash mechanism and the storage cache model are recorded and verified;
- the rig can power off without a graceful guest shutdown;
- every valid trial keeps a pre-recovery disk image;
- the `$LogFile` decode and the recovery check are repeatable;
- every trial has an independently recorded manifest of confirmed operations;
- both the large and the small topology have results;
- every primary arm reaches the required number of valid trials, or the report
  states explicitly why it could not;
- all failed and invalid trials are retained and explained;
- the final report separates the recovery behaviour actually observed from the
  guarantees the experiment cannot prove.

## 15. Explicit non-goals

E34 must not claim:

- a general NTFS durability guarantee;
- that every candidate flush call writes new `$LogFile` pages;
- that an ETW flush event alone proves persistence to stable media;
- that terminating a VM is a physical power cut without a verified storage cache
  model;
- that one successful crash trial proves every future crash timing is safe.

## 16. Deliverables

- `E34-PLAN.md` (this file) and `E34-RESULTS.md`;
- reproducible workflow / scripts;
- the external manifest of every trial;
- the pre-recovery disk image per trial, or its full hash and storage location;
- the pre-recovery `$LogFile` decode;
- the post-recovery namespace report;
- machine-readable aggregate JSON / CSV;
- artifact links and the exact workflow run id.

The final conclusion must answer, **per handle, per operation type and per
directory topology**:

> After a candidate flush API returned successfully and power was lost
> immediately, does NTFS log replay preserve the confirmed directory-index
> operation under the storage model tested here?

The results of the arms must not be merged into one general claim.
