# E33 — NTFS `$LogFile` flush attribution for directory-index (`$I30`) changes

> **Superseded (2026-11-24) by [E33.1-RESULTS.md](E33.1-RESULTS.md).**  The numbers
> below come from a hand-rolled local analysis of run 45254468807.  E33.1
> ([run 45297618762](https://github.com/takomitkm/leveldb/actions/runs/45297618762))
> re-ran the experiment with the attribution fixed and the analysis inside CI:
> the target-page matching must use the *physical* RCRD page a record occupies
> (the LSN-derived page can differ in a circular log), and the barrier test
> must be per candidate call rather than per arm.  E33.1 confirms the
> conclusions below but changes their evidence basis; read E33.1 for the
> numbers that stand.


**Run:** 2026-11-24, GitHub Actions `windows-latest` (Windows Server 2025,
build 26100), volume `D:` NTFS, 20 reps × 3 operations × 12 arms.
**Workflow run:** [45254468807](https://github.com/takomitkm/leveldb/actions/runs/45254468807) (head `f0a9e0b`; the full-page decoder fix `62f440a` was validated locally on the same artifacts).
**Branch:** `e33-ntfs-i30-log-attribution`.
**Plan:** [E33-PLAN.md](E33-PLAN.md). **Tier:** 1 (no power cut).

## What was measured

Three stages were kept separate: (1) does NTFS log the `$I30` change, (2) do
the corresponding `$LogFile` pages reach the storage stack, (3) do they do so
behind a flush barrier before the candidate API returns. The rig extracts the
raw `$LogFile` image after each arm by reading the volume device `\\.\D:`
(the metadata files cannot be opened by name even as admin), decodes every
RCRD page, and correlates the decoded LSN pages with `DiskIo/Write`
`ByteOffset` events and `DiskIo/FlushBuffers` timestamps from the same arm's
wpr DiskIO session. Only the namespace operation and the candidate flush were
inside the measured window; fixture construction, durable pre-state creation,
handle opening and metadata extraction were outside it.

The decoder is validated on a real 64 MiB `$LogFile` independently (79,618
records, 68,304 index operations, `AddIndexEntryRoot`, `AddIndexEntryAllocation`,
`DeleteIndexEntryAllocation`, `UpdateFileNameAllocation`, ...). Each arm's
snapshot contains that arm's unique `e33_<ARM>_*` fixture names at a strictly
advancing LSN range (A ≈ 34.88M → L ≈ 35.44M), which is how attribution was
confirmed against the shared volume's accumulated log.

## Open handles (new capability result)

| Path | Without `FILE_FLAG_BACKUP_SEMANTICS` | With `FILE_FLAG_BACKUP_SEMANTICS` |
|---|---|---|
| `D:\e33\small:$I30:$INDEX_ALLOCATION` | `ERROR_ACCESS_DENIED` (5) | **opens** — `GENERIC_READ\|GENERIC_WRITE` (`0xC0000000`) and `FILE_READ_DATA\|FILE_WRITE_DATA` (`0x3`) both succeed |
| `D:\e33\small::$I30:$INDEX_ALLOCATION` | `ERROR_ACCESS_DENIED` (5) | **opens** |
| `D:\e33\small:$I30` (`$INDEX_ROOT` alias) | `ERROR_FILE_NOT_FOUND` (2) | `ERROR_FILE_NOT_FOUND` (2) — not independently openable |
| `D:\$LogFile` | `ERROR_ACCESS_DENIED` (5) | not opened (same) |
| `D:\$MFT` | `ERROR_ACCESS_DENIED` (5) | not opened (same) |
| `D:\` root | opens with `GENERIC_READ\|FILE_APPEND_DATA` | opens |
| `\\.\D:` | opens with `GENERIC_READ` | opens |

`NtCreateFile` on the stream returned `0xC000003B` (`STATUS_OBJECT_PATH_SYNTAX_BAD`,
dos 161) for all access masks and options tried — the Win32 path normalizer
is required for the `:$I30:$INDEX_ALLOCATION` syntax.

**Consequence:** the earlier "stream handle err=5" reading was a probe bug
(missing backup semantics), not a capability limit. The explicit stream handle
is openable and was exercised in arms F–I.

## Experiment results

`API ok` counts the 60 candidate calls per arm that succeeded. `target pages`
= distinct `$LogFile` pages holding decoded index records for that arm's
`e33_<ARM>_*` names. `page writes` = `DiskIo/Write` events landing on those
pages (after solving the volume-offset mapping from the same ETL). `flushes
inside / after` = `DiskIo/FlushBuffers` events between the first and last
target write / after the last target write.

| Arm | Handle → API | API ok | Index opcodes (target name) | Target pages | Page writes | Flushes inside / after | Avg call | Level |
|---|---|---:|---|---:|---:|---:|---:|---|
| A | none → no flush | 0/60 | `Add/DeleteIndexEntryAllocation`, `AddIndexEntryRoot` | 45 | 19 | 1 / 1 | 0.017 ms | L1 (background writes only) |
| B | new file → `FlushFileBuffers` | 60/60 | `Add/DeleteIndexEntryAllocation` | 47 | 60 | 59 / 2 | 0.265 ms | **L3** |
| C | parent dir → `FlushFileBuffers` | 60/60 | `Add/DeleteIndexEntryAllocation` | 49 | 58 | 63 / 26 | 0.227 ms | **L3** |
| D | parent dir → `NtFlushBuffersFile` | 60/60 | `Add/DeleteIndexEntryAllocation` | 46 | 60 | 59 / 3 | 0.189 ms | **L3** |
| E | parent dir → `NtFlushBuffersFileEx(0)` | 60/60 | `Add/DeleteIndexEntryAllocation` | 46 | 54 | 57 / 4 | 0.217 ms | **L3** |
| F | **`$I30` stream → `FlushFileBuffers`** | 60/60 | `Add/DeleteIndexEntryAllocation` | 46 | 58 | 59 / 1 | 0.174 ms | **L3** |
| G | `$I30` stream → `NtFlushBuffersFile` | 60/60 | `Add/DeleteIndexEntryAllocation` | 44 | 65 | 128 / 1 | 0.173 ms | **L3** |
| H | `$I30` stream → `NtFlushBuffersFileEx(0)` | 60/60 | `Add/DeleteIndexEntryAllocation` | 48 | 60 | 59 / 1 | 0.157 ms | **L3** |
| I | `$I30` stream → `NtFlushBuffersFileEx(8)` | 60/60 | `Add/DeleteIndexEntryAllocation` | 47 | 61 | 62 / 0 | 0.164 ms | L2 (no trailing barrier in this window) |
| J | `$LogFile` handle → `FlushFileBuffers` | 0/60 | `Add/DeleteIndexEntryAllocation` | 46 | 10 | 0 / 1 | 0.035 ms | L1 (handle unavailable) |
| K | volume root → `FlushFileBuffers` | 60/60 | `Add/DeleteIndexEntryAllocation` | 52 | 120 | 141 / 1 | 6.486 ms | **L3** (positive control) |
| L | `\\.\D:` → `FlushFileBuffers` | 0/60 | `Add/DeleteIndexEntryAllocation` | 46 | 9 | 0 / 0 | 0.106 ms | L1 (call fails) |

Full decoded evidence, per-arm LSN ranges, and the ETW CSV/ETL artifacts are
in the workflow artifacts (see links below). `e33_integrate.py` writes the
raw join; `final-attribution.json` in this report's analysis package holds
the page-write/barrier table above.

## Reading the result

1. **Creation itself logs the index change.** Every arm's snapshot contains
   `AddIndexEntryAllocation` / `AddIndexEntryRoot` records carrying the
   test file names, before any flush is considered. Stage 1 is satisfied
   universally.
2. **The `FlushFileBuffers(parent_dir)` arm (C) does reach L3 here.** Its
   58 target-page writes with interleaved barriers are *above* the no-flush
   baseline (19 writes, 1 flush). This **revises E17/E21's "empty-set flush"
   reading at the LFS-log level**: the parent-directory flush is followed by
   target `$I30` log pages reaching the device behind a successful barrier.
   It does **not** mean the `$I30` home page was written — no arm produced
   attributable `INDX`/MFT home-page writes in this rig; E17's home-page
   finding stands.
3. **The explicit `$I30` stream handle is openable** with backup semantics,
   and flushing it (F/G/H) produces target log writes + barriers comparable
   to C/E. It is not a qualitatively different path at the LFS level.
4. **No arm reaches L4.** The `$I30` index pages / child MFT records were not
   observed as device writes in the measured windows. The namespace change
   is *log-durable* in the sense of stage 3, not *home-page-durable*.
5. **The positive control K is much more expensive** (6.5 ms/call vs
   ~0.2 ms for the dir/stream flushes) because a volume-root flush pushes
   all dirty volume metadata, not just the target log pages.
6. **Background writeback is real** (arm A: 19 target pages / 2 flushes), so
   a single extra write or flush is never treated as proof — the L3 callouts
   require the target page to be written and a barrier to follow it inside
   the same arm's window.
7. **`$LogFile` cannot be opened/flushed from user mode** (`ERROR_ACCESS_DENIED`
   by name; `NtCreateFile` on the stream alias fails at path parsing).
8. **`FLUSH_AND_PURGE` (flag 8)** returned `STATUS_SUCCESS` on 26100 but its
   arm (I) had no trailing barrier in this run; consistent with E32's
   "accepted but not demonstrably stronger".

## Answers to the 10 questions

1. **Does creating a file generate `$I30` logging before any flush?** Yes — decoded
   `AddIndexEntryAllocation`/`AddIndexEntryRoot` carry the test names in every arm.
2. **Does `FlushFileBuffers(parent_directory)` flush the target records?** At the
   *log* level yes (arm C: 58 target-page writes, 63 barriers inside the window);
   it still does not write the `$I30` home page.
3. **Can an explicit `$I30:$INDEX_ALLOCATION` handle be opened?** Yes, with
   `FILE_FLAG_BACKUP_SEMANTICS`; both path aliases work with `CreateFileW`.
4. **Does flushing that stream differ from flushing the parent directory?** No
   stable difference at the L3 evidence level (F/G/H ≈ C/D/E).
5. **Does `NtFlushBuffersFileEx(flags=0)` behave differently?** No; D/E/H all
   produce the same order of target writes and barriers as the Win32 flush.
6. **Does `FLUSH_AND_PURGE` behave differently?** Its call succeeds, but no
   stronger or more durable effect was observed here (arm I misses the trailing
   barrier; E32 also found no reproducible stronger effect).
7. **Can `$LogFile` be opened and flushed from user mode?** No — access denied.
8. **Does any API cause the `$I30` home page to be written?** Not observed in any
   arm; no L4.
9. **Which APIs reach L3?** B, C, D, E, F, G, H, and K (and marginally I at L2).
   Arm K remains a control, not a production recommendation (E26 withdrawal).
10. **What remains unproven?** Tier-2 crash/recovery (L5): whether a real power
    cut with these log pages durable actually reconstructs the directory entry.
    Device DRAM caching and NTFS recovery replay are outside this ETW-only run.

## Limitations / honesty notes

- Each `$LogFile` snapshot is taken after its arm's measured window; the
  volume is shared across arms, so earlier arms' records accumulate in later
  snapshots. Attribution uses each arm's unique `e33_<ARM>_*` names and the
  strictly advancing LSN ranges, not snapshot proximity.
- The physical-to-stream offset mapping was solved from the contiguous
  `$LogFile` page-write run in each ETL; the constant shift is a partition/
  device offset property. A 4 KiB write landing on a target page is counted
  as "page written"; sector-granular write-write ordering within a page is
  not resolved.
- `arm-A` demonstrates 19 target-page writes without any candidate flush —
  NTFS's own lazy writer writes log pages. The L3 classification is about a
  successful *barrier* after the write, not about the write being unique to
  the API.
- No power-cut test: L3 means "durable at the tested storage-stack boundary",
  not "survives power loss on every physical device".

## Artifacts

- Workflow: [45254468807](https://github.com/takomitkm/leveldb/actions/runs/45254468807) — `e33-artifacts-45254468807` (per-arm ETL/CSV, `open-probes.csv/json`, `E33-evidence.json`, `$MFT`/`$LogFile` images, report + log).
- Rig: `docs/powercut/e33/{run-e33.ps1,e33_extract.py,e33_logfile_decode.py,e33_mft.py,e33_etw_analyze.py,e33_integrate.py}`; workflow `.github/workflows/e33-tier1-ntfs-log.yml`.
- Decoder selftests: `python e33_logfile_decode.py --selftest`, `python e33_mft.py --selftest`.
