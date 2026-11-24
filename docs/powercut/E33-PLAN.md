# E33 — NTFS `$LogFile` flush attribution for directory-index (`$I30`) changes

**Status:** plan (results will land in `E33-RESULTS.md`)
**Date:** 2026-11-24
**Branch:** `e33-ntfs-i30-log-attribution`
**Tier:** 1 (GitHub Actions `windows-latest`, ETW + offline `$LogFile` decoding; no power cut)

## Question

E17/E21 established that `FlushFileBuffers(parent directory)` issues one
flush IRP per call but NTFS writes no MFT/index pages in response; E21 also
observed `$LogFile` RCRD-page writes in the parent-flush window (8 writes,
4 barrier-covered in the examined window) — but never decoded *which* log
records those were. E30/E31/E32 closed out the operation-level
write-through and `FLUSH_AND_PURGE` candidates.

E33 asks the attribution question directly:

> Which Windows handle/API makes the **exact** `$LogFile` redo/undo records
> of a test directory's `$I30` index reach the storage device behind a
> successful flush barrier?

Three separate stages (never conflated):

| Stage | Evidence |
|---|---|
| 1. Index modification logged | `AddIndexEntryRoot/Allocation`-family records for the target dir exist in `$LogFile` |
| 2. Log record persisted | the log pages holding those LSNs were written to disk in the measured window (ETW) |
| 3. Log flushed behind a barrier | those page writes are followed by a successful `DiskIo/FlushBuffers` before the candidate API returns |
| 4. Home page written | the `$MFT` record / `$INDEX_ALLOCATION` page of the target dir was written (ETW) |
| 5. Crash recovery | Tier-2 only (QEMU/`blklogwrites`); out of scope here |

## Arms

| ID | Handle | API |
|---|---|---|
| A | none | no flush (negative control) |
| B | new file | `FlushFileBuffers` (data control, E21 shape) |
| C | parent dir | `FlushFileBuffers` (E17/E21 baseline) |
| D | parent dir | `NtFlushBuffersFile` |
| E | parent dir | `NtFlushBuffersFileEx(flags=0)` |
| F | `$I30:$INDEX_ALLOCATION` stream | `FlushFileBuffers` — **primary** |
| G | `$I30` stream | `NtFlushBuffersFile` |
| H | `$I30` stream | `NtFlushBuffersFileEx(flags=0)` |
| I | `$I30` stream | `NtFlushBuffersFileEx(flags=8)` (exploratory, 24H2+) |
| J | `$LogFile` itself | `FlushFileBuffers` (if openable) |
| K | volume root | `FlushFileBuffers` (positive control) |
| L | volume device `\\.\D:` | `FlushFileBuffers` |

Both documented stream path aliases are probed:
`D:\...\dir:$I30:$INDEX_ALLOCATION` and `D:\...\dir::$INDEX_ALLOCATION`.
A failed open is a **result**, not a skip: exact GetLastError/NTSTATUS and
access mask are recorded.

## Fixtures

- **small** directory: resident `$INDEX_ROOT` (verified from the extracted `$MFT`).
- **large** directory: pre-populated with 2000 entries to force a
  non-resident `$I30:$INDEX_ALLOCATION` (verified from the extracted `$MFT`,
  not assumed from a count).

Three namespace operations per measured window, each rep with a unique name:

1. **create** — `CreateFileW` + 2048 B write → candidate flush;
2. **rename** — same-directory `MoveFileExW` of a durabilized source (the
   E17 pre-state discipline) → candidate flush;
3. **delete** — `DeleteFileW` of a durabilized target → candidate flush.

The measured window contains *only* the namespace ops and candidate calls
(E21 discipline; setup, fixture build, handle opens, and metadata extraction
stay outside).

## Decoding, not counting

The central deliverable is the decoded `$LogFile` evidence, not RCRD write
counts. `e33_logfile_decode.py` (self-contained, stdlib-only, round-trip
selftest) parses the raw `$LogFile` image extracted after each arm:

- restart area (v1.1 and 2.0+ fast-page layouts), USA fixups;
- client record chains from the client restart LSNs;
- per-record redo/undo opcodes with names (`AddIndexEntryRoot`,
  `DeleteIndexEntryAllocation`, `WriteEndOfIndexBuffer`, ...);
- target attribution: resident ops → FRS number (`target_vcn` /
  `cluster_block_off` / block size); nonresident ops → open-attribute-table
  (`OpenNonresidentAttribute` + `OpenAttributeTableDump` /
  `AttributeNamesDump` resolution);
- index-entry payloads decoded as `NTFS_DE` + `$FILE_NAME` keys → the
  **actual child file name and parent record** carried by each add/delete.

A hit is only attributed when the decoded name/MFT reference matches the
test fixture; unresolved records stay raw. `$UsnJrnl:$J` is not consulted.

## Separated counters

- **Counter A — `$LogFile` writes:** DiskIo writes landing in the LCNs of
  `$LogFile` (mapped from `$MFT` record 2's data runs via `e33_mft.py`).
- **Counter B — home-page writes:** writes landing in the fixture
  directory's `$MFT` record cluster (resident `$INDEX_ROOT`) or
  `$INDEX_ALLOCATION` extents (non-resident `$I30`).

All-MFT-region writes are never counted as target-index writes.

## Classification

| Level | Requirement |
|---|---|
| L0 | no attributable index records / home-page writes |
| L1 | target `$I30` opcodes present in `$LogFile` (persistence unproven) |
| L2 | corresponding log page written during the measured window |
| L3 | L2 + successful flush barrier before the API returned |
| L4 | L3 + home page written behind a barrier |
| L5 | Tier-2 crash recovery (separate rig, not here) |

An arm is never promoted without the lower level's evidence. ETW-only runs
never claim power-loss durability.

## Rig files

- `run-e33.ps1` — open-probe matrix, fixtures, per-arm wpr sessions,
  `$MFT`/`$LogFile` extraction, ops CSV;
- `e33_logfile_decode.py` — the `$LogFile` decoder (selftest: `--selftest`);
- `e33_mft.py` — raw `$MFT` parser (selftest: `--selftest`);
- `e33_etw_analyze.py` — tracerpt CSV counting per arm;
- `e33_integrate.py` — joins decoder output with ETW counts;
- `.github/workflows/e33-tier1-ntfs-log.yml` — the CI workflow.

## Threats to validity (pre-registered)

- Extracted `$MFT`/`$LogFile` come from the volume *after* the window; a
  clean-ish dismount state can persist records that the tested API never
  flushed. Timing claims come only from ETW.
- The `$LogFile` write that carries the target LSN page may coincide with
  background log flushes; attribution requires LSN→page-offset mapping
  (decoder emits `stream_offset` for nonresident ops), not proximity.
- A single-run positive is rerun before any conclusion (E29 discipline).
- `NtFlushBuffersFileEx(flags=8)` is feature-detected per build.
