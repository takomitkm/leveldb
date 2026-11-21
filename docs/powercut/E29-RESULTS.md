# E29 — operation-level write-through at device level: the journal verdict (2026-11-21)

**Question** (closes the operation-level `FILE_WRITE_THROUGH` arm that the
master index scheduled as E27, "the main open question"): does making the
*handle that performs the namespace change itself* write-through —
`MoveFileExW(..., MOVEFILE_WRITE_THROUGH)`, or the rightkit shape
(`NtCreateFile` / `NtSetInformationFile` on a `FILE_WRITE_THROUGH` handle) —
put the namespace change on the device with a barrier, i.e. behave like
POSIX `fsync(dir)`?

**Answer: no, at both measurement layers.** The ETW layer (E27/E28) shows no
flush IRP and no metadata burst for the rename/delete shapes; the power-cut
journal layer (this document, arms 30–33) shows zero reproducible barriers —
namespace changes reach the device only through NTFS internal writeback
timing, never barrier-covered. The one FLUSH that initially looked
attributable did not reproduce in two reruns.

## Layer 1 — ETW (E27/E28, 2026-11-20)

GitHub `windows-latest` runners, `wpr` DiskIo tracing, 100 reps per arm, two
independent runs (sources: branches `e27-rightkit-ftw` /
`e28-rightkit-single-ops`; the single-operation consumers link the unmodified
`ejm-rightkit-fs` crate). Arm definitions from `run-e27.ps1` / `run-e28.ps1`;
raw counts (writes / flushes per 100 reps):

| arm | shape | E27 | E28 |
|---|---|---|---|
| A | create file, no flush (floor) | 179 / 8 | 68 / 6 |
| B | create + `FlushFileBuffers(file)` | 290 / 206 | 280 / 204 |
| C | B + `FlushFileBuffers(parent dir)` | 178 / 404 | 264 / 404 |
| D | B + `FlushFileBuffers(volume root)` — positive control | 450 / 408 | 443 / 408 |
| E | `NtCreateFile(FILE_CREATE, FILE_WRITE_THROUGH)` + write + close | 597 / 4 | 294 / 8 |
| F | open source with `FILE_WRITE_THROUGH`, `NtSetInformationFile(FileRenameInformation)` | 291 / 5 | 174 / 3 |
| G | open target with `FILE_WRITE_THROUGH`, `NtSetInformationFile(FileDispositionInformation)` | 198 / 6 | 175 / 3 |
| H | unmodified rightkit-fs crate, atomic write via its public API | 917 / 206 | 490 / 206 |

- F/G sit at the flush floor (3–6 per 100 reps) in **both** runs: no flush
  IRP for the namespace change, no metadata burst behind it — while the
  positive control (D) proves the rig sees flush bursts (404–408).
- E is the one unstable row: 597 vs 294 writes for the same shape across the
  two runs. The E27 "+307 vs B" is runner variance, not a reproducible
  signal. (Note: E is a **file**-create analog; the mkdir shape with
  `FILE_DIRECTORY_FILE | FILE_WRITE_THROUGH` has not been run anywhere.)
- H's ~206 flushes are the crate's temp-file flush stage inside its atomic
  write; the rename inside that composite produced nothing on top.

## Layer 2 — power-cut journal (E29 arms, 2026-11-21)

Same rig as E17 (QEMU/TCG + WinPE 19041, data disk under `blklogwrites`,
arm id via SMBIOS serial, one arm per boot, guest killed after the DONE
marker; probe = `e17pe.cpp` with four added arms, compiled with Debian
mingw g++ 12-win32 in the CI guest). New measurement layer on top of E17:
the journal is replayed offline at two cut points per rep — worst = the
state guaranteed by the last FLUSH/FUA barrier the guest issued before the
closing marker; best = everything the device received — and trial names are
located in the journal as UTF-16LE `$FILE_NAME` / `$I30` bytes, so every
namespace write is ordered relative to the barrier sequence. Autopsy as in
E17 (ntfs-3g `ro,norecover` + `rw` replay). Four arms × 3 reps, all
`errs=0,0,0`, all `how=S` (SMBIOS).

Probe sequences, in full:

- arm 30 — `CreateDirectoryA(dir)` + a 2048-byte file inside the new
  directory; no flush of any kind (`CreateDirectory` has no write-through
  variant).
- arm 31 — write src (no flush) → `MoveFileExW(…, MOVEFILE_REPLACE_EXISTING |
  MOVEFILE_WRITE_THROUGH)`.
- arm 32 — write file (no flush) → `DeleteFileW`.
- arm 33 — write tmp with `FlushFileBuffers` → the same
  `MOVEFILE_WRITE_THROUGH` rename.

| arm | guaranteed at op return (worst cut) | on device after the cut |
|---|---|---|
| 30 | no | dirs present, file inside absent |
| 31 | **no** | dst present, src gone from the index; the MFT record still carries both names (link count 2) |
| 32 | no — the deleted file is still on the device at the rep-0 best cut | nothing visible (the net-zero index change is never published; even the marker files' index entries are absent) |
| 33 | tmp data yes (its own `FlushFileBuffers`); **rename no** | dst present, tmp absent (link count 2, same stale-MFT shape) |

### The decisive sequence, and its reproduction

The rename arm's journal window contains **no FLUSH command**. Run 1 showed
a single FLUSH (record 124) right after the rename's metadata writes
(records 119–123), which initially looked attributable to
`MOVEFILE_WRITE_THROUGH`. Two reproduction runs of the same arm (same
binary, ISO and base image; `e29v2-log-a31-run{1,2,3}.img`) had **zero**
flush records in the trial windows, so the run-1 flush is a
nondeterministic NTFS internal event (checkpoint / `$LogFile` pressure
timing), not part of the rename's contract. Arms 30 and 32, which contain
no flush-capable call at all, show zero flush records across their whole
runs — ruling out periodic checkpoints as the source of anything observed.

Net: `MOVEFILE_WRITE_THROUGH` — zero reproducible barriers in four samples
(three dirty-source runs + one clean-source run). Namespace changes reach
the device only through writeback timing; nothing is guaranteed at op
return. The durable element of the atomic-write composite is the temp
file's own `FlushFileBuffers` (data + metadata); the publication rename is
unbarriered, so "the new value survives the power loss" is writeback luck.
The composite is still crash-safe in the usual sense (old-or-new, never
torn; a lost rename is replayable), but that is atomicity, not durability.

### Reconciliation with the family

- E14 arm 4 / E17 arm 6 ("no flush and no FUA") — confirmed, now with the
  reproduction caveat that explains how a single-run reading can mislead.
- rightkit-fs `windows.rs` claims *"FILE_WRITE_THROUGH on the handle that
  creates a directory, renames or deletes … so they commit with the call.
  A power-loss test has not been run against that claim."* — the ETW layer
  shows no flush IRP and no metadata burst for rename/delete, and the
  mechanism that claim rests on ("the same mechanism
  `MOVEFILE_WRITE_THROUGH` relies on") measures negative at journal level.
- The only mechanism in this whole family that pushes namespace metadata to
  the device **with** a barrier remains `FlushFileBuffers` on the volume
  root (E17 arms 11/12/14/15) — withdrawn for privilege reasons in E26.

### Scope gap, recorded rather than papered over

The E29 power-cut arms ran the **Win32 equivalents** of the rightkit shapes
(`MoveFileExW` write-through, plain `CreateDirectoryA` / `DeleteFileW`);
the E29 ISO carried the rightkit single-op binaries, but the wrapper arms
did not launch them. The journal-level verdict above therefore covers the
flag and the plain ops; the exact NT-level shapes (E27-F/G) have ETW-level
evidence only. Running those through the journal rig is the natural E30.

## Rig findings worth recording

- The zero-marker boots that blocked E29 for a day had a single root cause:
  the Actions-built bundle's `winpeshl.ini`
  (`[LaunchApps]` + bare `e29pe.exe`) overwrote the proven E14 launcher
  (`[LaunchApps]` + `X:\e13pe.exe`) on the CI guest, and winpeshl.ini
  suppresses `startnet.cmd` — so neither the probe launch nor `wpeinit`
  ever ran. The v8 fix keeps the tree17 boot files byte-identical (md5
  verified post-build) and changes only the probe binary and the added
  PEs. Dependency hypotheses were eliminated first: both probe flavors
  (MSYS2 UCRT and Debian mingw MSVCRT) resolve every import on WinPE 19041.
- Two files in the E29 lineage were destroyed by shell escaping and both
  were caught by byte-level inspection (`od -c`): the drafted
  `startnet_e29.cmd` contained `X:` + `ESC` + `29pe.exe` (the `\e` of
  `X:\e29pe.exe` eaten), and the first patch generation emitted
  `"%s\rk30_r%d"` in C, where `\r` compiles to a carriage return inside the
  trial path. All generated boot/probe files now go through file transfer +
  byte-level verification, never shell interpolation.

## Artifacts

- E27: Actions run 44748277787, branch `e27-rightkit-ftw`.
- E28: Actions run 44751173356, branch `e28-rightkit-single-ops`.
- E29 journal: `e29v2-{base,vm,log}-a{30,31,32,33}.img` on the school
  machine (`D:\yzd\leveldb-e2e-yhPf93BW`), arm-31 rerun logs
  `e29v2-log-a31-run{1,2,3}.img`; replay tooling `e29_verdict.py` /
  `e29_win.py` (same `e14_analyze.load_records` parser as E17).
- ISO: `pe-e29.iso` == `pe-e17.iso`, 765034496 bytes,
  sha256 `8a0b03325e3d995c17ee8a45…` (v8 minimal-delta build; boot files
  byte-identical to tree17).
- Autopsy transcripts and window dumps: local working notes
  (`e29-autopsy-out.txt`, `e29-verdict-report.txt`, `e29-repro-out.txt`).

Scope: WinPE 19041 and a Windows Server 2025 runner volume, QEMU IDE/TCG;
Win11 `ntfs.sys`, NVMe/virtio device models, ReFS, SMB and a physical power
cut (Tier-2) remain untested.

## Follow-up (2026-11-21)

The two gaps recorded above are now closed, both negative: the exact NT-level
shapes (E27-F/G) were run through the journal rig as **E30**
([E30-RESULTS.md](E30-RESULTS.md)), and rightkit-fs's public single
operations as **E31** ([E31-RESULTS.md](E31-RESULTS.md)). Separately,
`NtFlushBuffersFileEx(FLUSH_AND_PURGE)` on 24H2+ was measured as **E32**
([E32-RESULTS.md](E32-RESULTS.md)) — accepted, but no stable evidence it is
stronger than a plain flush.
