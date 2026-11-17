# Power-cut / block-tracing test rig (E11 / E14 / E17 / E19 / E21 / E23 / E24-E25)

This directory archives the measurement rigs behind the Windows
parent-directory-flush fix (`SyncDirIfManifest()`, upstream PR #1388) and
its README claims. Three generations, one question each:

| Rig | Question | Method | Result |
|---|---|---|---|
| **E11** (Linux QEMU power-cut model) | Does rename + directory `fsync` survive a power cut on a POSIX FS? | Linux guest (ext4), probe `e11-probe.c`: rename → optional `fsync(dir)` → host kills QEMU → reboot autopsy | sync arm 5/5 SURVIVED, nosync arm 8/8 LOST (`e11_v2.sh` fixed the v1 harness bug where unsynced binaries were judged). Demonstrates the mechanism discriminates; **Linux evidence, not Windows** |
| **E14/E15** (Windows block-command tracing) | On NTFS, which operation actually puts a namespace change on the device? | QEMU `blklogwrites` records every WRITE/FLUSH/FUA the guest issues; arm id via SMBIOS serial; the power-cut instant is an **offline cut** of the log (worst = up to the last barrier, best = everything the device received); ntfs-3g double-mount autopsy (ro,norecover = raw platter / rw = replayed). Ran on WinPE and full Windows 10 Pro 22H2, six arms, three reps each, identical results | `FlushFileBuffers(file)` covers data but not the directory entry; `MOVEFILE_WRITE_THROUGH` rename issues **no flush and no FUA**; the **directory-handle flush is the only observed operation that puts the namespace change on the device**. Full report: [E14-RESULTS.md](E14-RESULTS.md) |
| **E17** (per-DB shape verification, completed 2026-11-17) | Do the exact API sequences each database's Windows dir-flush patch uses (leveldb 30b34e0 / SQLite / LMDB / PostgreSQL / MySQL / FrankenSQLite) put the namespace change on the device — including **delete** and **mkdir**? | Same rig as E14, probe `e17pe.cpp` with 16 arms reproducing each patch's literal call sequence plus controlled disambiguation arms (flush parent dir vs flush volume root); error codes embedded in markers. **Re-tested on a full Windows 10 Pro 22H2 guest** (the E15 golden image, probe injected via a maintenance-boot installer arm) | **The five patches' shared shape — a single `FlushFileBuffers` on the parent directory — produced ZERO device-level metadata writes** (arms 1-5, 7-8, 13: file data barriered, namespace not on platter). The platter effect comes specifically from `FlushFileBuffers` on the **volume root** (arm 11: root flush alone persists the file; arm 13: parent-dir flush twice does nothing). Corrected protocols verified: rename + parent+root flush persists the dst (arm 14), durable delete + parent+root flush persists the deletion (arm 15). **All five key arms reproduce byte-for-byte on the full Windows stack** (same MFT records, same platter verdicts). **This revises E14's attribution**: E14's positive signal came from its pre-state's root flush, not from the parent-directory flush. Full report: [E17-RESULTS.md](E17-RESULTS.md) |
| **E19** (Linux + NTFS, completed 2026-11-17) | On Linux with ntfs-3g, does the POSIX rule "fsync the parent directory after create/rename" actually matter, or is it filesystem-specific? | E11 protocol on the E17 NTFS data disks: Debian 12 guest, three NTFS volumes under separate blklogwrites, ntfs-3g rw mounts; probe `e19lin.c` = fsync pre-state, create 2048B file, arm-specific flush; 1 s window then host kills QEMU; autopsy = 3 read-only originals + 3 rw copies, ro,norecover vs rw replay | **`fsync(file)` alone already persists the directory entry on ntfs-3g** (arms 0 and 1 identical: file on the platter); `fsync(dir)` adds nothing; the no-flush teeth arm loses the file (model discriminates). **Opposite of ext4** (E11: fsync(file) 8/8 lost, fsync(dir) required) — so "must fsync the parent dir" is a per-filesystem-driver contract, not a universal POSIX guarantee. Full report: [E19-RESULTS.md](E19-RESULTS.md); Windows+ext4 (E20) assessed as not executable (no supported driver, non-admin host, and a third-party FSD would not generalize) |
| **E21** (real Windows kernel + GitHub Actions, completed 2026-11-17) | On real hardware, does `FlushFileBuffers(parent dir)` reach the device at all - and does it make the namespace durable? | `run-tier1.ps1` + a workflow on `windows-latest`: four cumulative arms (no flush / flush file / + flush parent dir / + flush root), each in its own wpr DiskIO ETW session, **measurement window contains only the flush calls**, 100 reps per arm; counts `DiskIo/FlushBuffers` and `Write` events | **The parent-dir flush is not a no-op, but it flushes an empty set.** Flush IRPs per call: +1.00 for both the parent-dir and the volume-root flush (identical at the storage stack). Write events per call: **~0.1 for the parent dir vs ~1.3-3.1 for the volume root** (10x apart, four 100-rep runs). NTFS does not write the new file MFT record or directory index in response to a directory-handle flush, so the flush has nothing to make durable - which reconciles MS-FSA 2.1.5.7 <80> (the directory flush exists) with the QEMU result (it changes nothing). Full report: [E21-RESULTS.md](E21-RESULTS.md) |
| **E23** (per-commit cost, completed 2026-11-18) | What does each flush variant cost amortized per commit (SQLite pays once per transaction)? | `bench-sqlite-commit.ps1` + a workflow on `windows-latest` (volume D:): simulated rollback-journal commit sequence (create journal → 2 KiB non-resident write+fsync → db write+fsync → delete journal → variant flushes), 200 transactions per variant + 20 warmup | **dir flush is free** (−0.016 ms/commit, noise — cheap precisely because it does no work), **root flush +1.473 ms/commit** (≈2×, TPS 698→344) — the unit price of namespace durability on Azure NVMe. Leveldb amortizes one flush per 1–4 MB (acceptable); SQLite pays per transaction (B1 vs B2 decision). Only the deltas are the signal; absolute TPS is not (the sim omits SQLite parsing/B-tree/locking). Results: [E21-RESULTS.md](E21-RESULTS.md) (E23 section) |
| **E24/E25** (real-build costs, completed 2026-11-18) | What do the flushes cost on **real SQLite and real LMDB** builds, where each engine pays at a different rate? | E24: three SQLite amalgamation variants (base / dir / dir+root) built from the same source, commit-shaped bench + speedtest1 + the dirsyncprobe rig upgraded for the root flush (`-DEXPECT_ROOT_FLUSH`, S8 expectations computed from the resolved name) + a hook counter proving the hook fires. E25: three LMDB variants (pristine / dir / dir+root), env-creation and commit phases, windows+linux jobs, probe with `DIRSYNC-PROBE-ROOT` line | **E24 (SQLite, synchronous=FULL): dir flush free (+0.06 ms/txn), dir+root +5.4 ms/txn (≈5.2×, TPS 795→150)**; EXTRA doubles it exactly (2×5.3); WAL mode zero tax; speedtest1 mixed workload +35–50%. The real per-flush price (5.4 ms) brackets with E23's sim (1.47 ms) — device- and dirty-metadata-dependent. **E25 (LMDB): the patch fires at env_open/env_copy only — dir+root +2.5–3 ms per fresh env, commit path untouched (phase B equal across variants), root flush works non-admin on the runner (`root_err=0`), red side intact.** Report: [E24-RESULTS.md](E24-RESULTS.md) |

## What ran where

- Host: Windows 11 x64, QEMU/TCG (software emulation), IDE disks — chosen so
  every guest-issued block command is visible to `blklogwrites`. FUA semantics
  on real NVMe hardware were **not** tested.
- Guests: WinPE and Windows 10 Pro 22H2 (E14/E15); Debian (E11, and as the
  ntfs-3g autopsy worker for E14/E17).
- The Windows data volume is a raw NTFS image labelled `E13SYS`; the power cut
  is `Stop-Process -Force` on QEMU after the guest writes its `E14DONE` /
  `E17DONE` marker (markers are `FILE_FLAG_WRITE_THROUGH`, so the marker
  itself is visible in the log and the kill happens right after it).

## File map

### E11 (Linux model)
- `e11-probe.c` — guest probe: create/fsync two files, rename, optional dir fsync
- `qemu_e11.sh` — host-side single-boot driver (upload, compile, boot, kill, record)
- `e11_v2.sh` — the corrected multi-trial harness (verifies probe bytes, records rc; v1's "5 LOST" were a harness bug, which is itself evidence the model detects unsynced writes)

### E14/E15 (Windows tracing, six arms 0-5)
- `e14pe3.cpp` — the probe (create / flush-file / +flush-dir / rename / rename+WRITE_THROUGH / create+WRITE_THROUGH; SMBIOS arm config; 2048-byte non-resident marker bodies so every op is content-addressable in the block log)
- `e14v3-launch.ps1` — QEMU launcher (WMI-started; `blklogwrites` node wiring)
- `e14v3-run.sh` — single-arm runner: fresh base copy, boot, poll log for DONE, force-kill, keep base/vm/log images
- `e14v3_gbld.sh` — guest-side ISO rebuild (inject probe into boot.wim)
- `e14v3_mkbdata.sh` — master NTFS data-disk builder
- `e14_analyze.py` — `blklogwrites` log decoder + offline replay (with selftest)
- `e14v3_verdict.py` — per-arm cut selection (worst/best), crash-image builder, log-level verdict
- `e14v3-autopsy.sh` — Debian-side ntfs-3g double-mount autopsy
- `E14-RESULTS.md` — the full result report (six-arm table, autopsy, caveats)

### E21 (real hardware / CI, `run-tier1.ps1` + `.github/workflows/e21-tier1.yml`)
- `run-tier1.ps1` — four cumulative arms with a flush-only measurement window; prefers wpr (its DiskIO profile emits `DiskIo/FlushBuffers`), logman as fallback; reports flush IRPs, writes, reads and per-call durations; verdict keys on writes (a dir flush that carries metadata would show writes)
- `E21-RESULTS.md` — the measurements, the four-run consistency table, and the two logman gotchas (multiple `-p` is rejected; the StorPort provider carries no SRB detail)

### E23 (per-commit cost, `bench-sqlite-commit.ps1` + `.github/workflows/e23-sqlite-commit-cost.yml`)
- `bench-sqlite-commit.ps1` — P/Invoke commit-sequence simulator: three variants (vanilla / +dir / +dir+root), warmup + measured loop, prints per-commit ms and TPS with deltas
- Results live in the **E23 section** of `E21-RESULTS.md`

### E24/E25 (real-build costs; rigs live on the patched repos' branches)
- E24 (SQLite): `e24_root_patch.py` (fail-closed variant generator), `e24_bench.c` (commit-shaped bench with the `sqlite3_win_dir_sync_count` hook counter), the upgraded `dirsyncprobe.c` (`-DEXPECT_ROOT_FLUSH`, computed S8 expectations), `bench.yml` — all on **takomitkm/sqlite, branch `bench`**; results in [E24-RESULTS.md](E24-RESULTS.md)
- E25 (LMDB): the root-flush patch + `mdb-base/mdb-dir/mdb-dirroot.c` variants, `e25_lmdb_bench.c` (env-creation + commit phases), `dirsync-test.c`/`dirname-test.c`/`race-test.c`, `dirflush.yml` — on **takomitkm/lmdb, branch `dirflush`** (probe emits a `DIRSYNC-PROBE-ROOT` line); results in [E24-RESULTS.md](E24-RESULTS.md)

### E17 (per-DB shapes, sixteen arms 0-15 + installer arm 90, completed 2026-11-17)
- `e17pe.cpp` — probe reproducing each DB patch's literal API sequence plus the flush-target disambiguation arms (11-15); arm 90 = installer (copies the probe into a full-Windows golden image's ONSTART task path during a WinPE maintenance boot)
- `e17-run.sh` / `e17-launch.ps1` / `e17-all.sh` / `e17-start.ps1` / `e17_gbld.sh` / `e17-phase1.sh` / `e17-phase1b.sh` / `e17-phase1c.sh` / `e17-phase2.sh` / `e17-autopsy-all.sh` / `e17-autopsy.sh` / `e17-autopsy-start.ps1` — same rig plumbing, E17 naming (the ISO is rebuilt from the proven pe-e14v3.iso tree: xorriso extract + dd of the hidden El Torito image + WIM probe swap)
- `e17w10-*.sh` / `e17w10-*.ps1` — full-Windows re-test pipeline (E15 golden image + per-arm qcow2 overlay + maintenance boot installer + arm runner; arms 1/11/13/14/15)
- `e17_verdict.py` — verdict driver for arms 0-15 (PREFIX/ARMS parameterized)
- `e17_mftzone.py` — per-arm MFT-zone (<8 MiB) write counter, split by rep window vs park window (PREFIX/ARMS parameterized)
- `E17-PLAN.md` — arm table, expected results, and the provenance notes for each DB's patch shape
- `E17-RESULTS.md` — the full result report: the parent-directory flush alone has no device-level effect; the volume-root flush does; corrected create/rename/delete protocols; revision of the E14 attribution; byte-for-byte reproduction on the full Windows 10 Pro 22H2 guest

Paths inside the scripts appear as `<HOSTDIR>` / `<TASKDIR>` / `<QEMUDIR>` /
`<guest>` placeholders — substitute your own layout (a Windows host with MSYS2
QEMU ≥ 9, a Debian guest for builds/autopsy, and a scratch directory for the
images).

## Caveats (inherited from E14-RESULTS.md)

- IDE/TCG stack: flush/FUA semantics may differ on real NVMe; arm 1 proves the
  guest→device flush path exists and fires when the OS decides to flush.
- ntfs-3g replay ≠ `ntfs.sys` replay; the platter-level byte findings are
  OS-independent, but crash-recovery behavior on real Windows is untested.
- Worst-case = "unbarriered writes may be lost"; best-case = everything the
  device received. Neither is a claim about real-hardware power loss.
