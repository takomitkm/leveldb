# E32 — `NtFlushBuffersFileEx(FLUSH_AND_PURGE)` on 24H2+: what it actually does (2026-11-21)

**Question**: `NtFlushBuffersFileEx` accepts a flags word that only phnt
documents (`FLUSH_FLAGS_FILE_NORMAL 0`, `FILE_DATA_ONLY 1`, `NO_SYNC 2`,
`FILE_DATA_SYNC_ONLY 4`, `FLUSH_AND_PURGE 8`). E17's NT matrix probed flag 8
on build 19041/19045 and got `STATUS_INVALID_PARAMETER` (the flag is 24H2+).
On a build that supports it (26100 / 26300), is `FLUSH_AND_PURGE` a *stronger*
flush than flag 0 or the Win32 `FlushFileBuffers` — i.e. does it push
volume-wide dirty metadata to the device, which would make it interesting as
a non-`GENERIC_WRITE` volume-level flush?

**Answer (three runs):** flag 8 is **accepted** on 26100 and behaves like a
**normal flush**, not a demonstrably stronger one. The per-call flush-IRP
rate is identical for every variant. The one run-to-run appearance of a
stronger root-handle effect did **not** reproduce in the third run — recorded
below exactly as measured, per the E29 discipline ("experiment conclusions
are only responsible for the combinations actually measured; a single-run
positive must survive a rerun").

## Rig

`docs/powercut/e32/run-e32.ps1` + `.github/workflows/e32-tier1-flushpurge.yml`;
GitHub Actions `windows-latest` (build 10.0.26100, Server 2025 kernel), data
volume `D:` NTFS, 100 reps per arm, cumulative arms, `wpr` DiskIO ETW
(same tracer discipline as E21/E27: setup outside the window, the measured
window contains only the flush calls; `tracer=wpr` confirmed in every run).

Arm shapes (dir/root handles opened `GENERIC_READ | FILE_APPEND_DATA` — the
non-elevated shape; GH runners are administrators):

| arm | call |
|---|---|
| A | baseline: create+write files, no flush |
| B | Win32 `FlushFileBuffers(file)` |
| C / D | `Ex(file, flags 0 / 8)` |
| E / F | `Ex(dir, flags 0 / 8)` |
| G / H | `Ex(volume root, flags 8 / 0)` |
| I | Win32 `FlushFileBuffers(volume root)` — the E17/E21 shape |

## Results (writes / flushes totals; Δ vs that run's baseline)

Run 1 — [44904378994](https://github.com/takomitkm/leveldb/actions/runs/44904378994):

| arm | writes | flushes | dW | dF |
|---|---|---|---|---|
| A | 57 | 4 | 0 | 0 |
| B | 269 | 203 | +212 | +199 |
| C | 267 | 202 | +210 | +198 |
| D | 280 | 205 | +223 | +201 |
| E | 216 | 267 | +159 | +263 |
| F | 176 | 202 | +119 | +198 |
| G | 309 | 210 | +252 | +206 |
| H | 195 | 207 | +138 | +203 |

Run 2 — [44904630237](https://github.com/takomitkm/leveldb/actions/runs/44904630237):

| arm | writes | flushes | dW | dF |
|---|---|---|---|---|
| A | 97 | 7 | 0 | 0 |
| B | 268 | 205 | +171 | +198 |
| C | 167 | 204 | +70 | +197 |
| D | 258 | 206 | +161 | +199 |
| E | 163 | 204 | +66 | +197 |
| F | 173 | 205 | +76 | +198 |
| G | 713 | 212 | +616 | +205 |
| H | 196 | 208 | +99 | +201 |

Run 3 (adds arm I) — [44906146787](https://github.com/takomitkm/leveldb/actions/runs/44906146787):

| arm | writes | flushes | dW | dF |
|---|---|---|---|---|
| A | 232 | 58 | 0 | 0 |
| B | 274 | 205 | +42 | +147 |
| C | 267 | 204 | +35 | +146 |
| D | 278 | 204 | +46 | +146 |
| E | 179 | 205 | −53 | +147 |
| F | 67 | 204 | −165 | +146 |
| G | 238 | 208 | +6 | +150 |
| H | 201 | 208 | −31 | +150 |
| I | 194 | 208 | −38 | +150 |

All `Ex` calls returned `STATUS_SUCCESS` on 26100 (flag 8 included); the
earlier 19041/19045 `STATUS_INVALID_PARAMETER` reading stands (E17).

## Reading

1. **Flush IRPs: identical for every variant.** All flush arms add ≈1.5
   flush events per call (run 3: +146..150 over 100 reps across flag 0,
   flag 8, file, dir, root, Win32); E21's independent measurement on the
   storage stack was +1.00/call for both parent-dir and root flush. Nothing
   in the tracer distinguishes `FLUSH_AND_PURGE` from a plain flush.
2. **Write counts: not reproducible across runs.** Runs 1–2 showed the
   volume-root flag-8 arm above the root flag-0 arm (309 vs 195; 713 vs 196);
   run 3 showed parity (238 vs 201, against a baseline that itself jumped to
   232 from 57/97 in earlier runs). Whole-run totals are dominated by
   run-to-run background writeback of the 100 dirty files; the apparent
   root-flag-8 advantage does not survive the third sample.
3. **Arm I (Win32 root flush) sits with G and H** in run 3 (194 vs 238/201):
   no evidence that flag 8 is stronger than the E17/E21 root flush.
4. Non-elevated local probe (this machine, build 26300, standard user):
   `Ex` returns `STATUS_SUCCESS` for flags 0/1/2/4/8 on a file handle, a
   directory handle and the `D:` volume root opened `GENERIC_READ |
   FILE_APPEND_DATA`; flag 1 (`FILE_DATA_ONLY`) is `STATUS_INVALID_PARAMETER`
   on dir/root handles, and a read-only root handle (`GENERIC_READ` only)
   fails the open with `STATUS_ACCESS_DENIED`. (C:\ root ACLs were not
   re-tested here; the E26/2026-11-21 criterion for C:\ — only
   `FILE_APPEND_DATA` opens non-elevated — stands.)

## What this does and does not establish

- Establishes: on 24H2+ builds the flag is callable, including on the volume
  root with a non-admin-compatible access mask, and it issues exactly one
  flush per call like every other flush shape; the earlier "not supported"
  reading was a build limitation, not a permanent one.
- Does not establish: any *stronger* durable effect. There is no stable
  write-burst signal beyond a plain flush in three runs, and no power-cut
  (Tier-2) evidence at all. `FLUSH_AND_PURGE`'s documented extra semantic
  (purging clean cached pages) would not show up as extra writes anyway, so
  this rig cannot see it.

## Rig findings

- Two probe bugs were caught by a local 26300 validation run before any GH
  rerun: positional `[IntPtr]::Zero` is passed as a literal string in
  PowerShell (all `Ex` calls failed with a parameter-binding error), and the
  file-handle access mask was `0x80000001` (read bit only) instead of
  `0xC0000000` (`GENERIC_READ | GENERIC_WRITE`), which made file flushes
  return `STATUS_ACCESS_DENIED` — itself a useful reminder that the flush
  right follows the handle's write access.

## Artifacts

- Branch `e32-flushpurge`: `docs/powercut/e32/run-e32.ps1`,
  `.github/workflows/e32-tier1-flushpurge.yml`; runs 44902911549 (broken
  probe, kept for the record), 44903371366 (header fix), 44904378994,
  44904630237, 44906146787 (final, incl. arm I).

Scope: build 26100 runner volume and 26300 local probe; ETW write/flush
counts only. NVMe-with-power-cut (Tier-2), ReFS, SMB, C:\ root ACLs under a
non-elevated token, and the purge semantics themselves remain untested.