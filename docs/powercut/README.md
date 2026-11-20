# Power-cut / block-tracing test rig (E11–E27; master index below)

This directory archives the measurement rigs behind the Windows
parent-directory-flush fix (`SyncDirIfManifest()`, upstream PR #1388) and
its README claims. Three generations, one question each:

## Master index (E11–E27)

> **E1–E10:** no archived record in this repository. The power-cut series as
> archived here starts at E11; do not infer results for earlier numbers from
> this table. **E18** was reserved for a patch-body run that never happened;
> **E20** was assessed as not executable; **E22** was inconclusive. The next
> free number is **E27**.

| ID | What it tested | Environment | Result |
|---|---|---|---|
| **E11** | Does rename + `fsync(dir)` survive a power cut on a POSIX filesystem? | Debian guest (ext4), QEMU/TCG, host kills QEMU, reboot autopsy | `fsync(dir)` 5/5 SURVIVED; no `fsync(dir)` 8/8 LOST. The model discriminates. Linux evidence, not Windows. |
| **E12** | Windows-native power-cut matrix attempt (WinPE ISO rebuild) | QEMU/TCG + WinPE on the school machine | Stopped without valid data: WinPE boot/black-screen/serial issues. No measurements. |
| **E13** | WinPE/QEMU power-cut with COM1 markers | QEMU/TCG + WinPE 19041 | No valid data: WinPE has no serial driver, and the host kill landed after the NTFS lazy writer; the no-flush teeth arm survived. Superseded by E14. |
| **E14** | Which operation actually puts a namespace change on the device? | QEMU `blklogwrites` + WinPE 19041; six arms ×3 reps | `FlushFileBuffers(file)` covers data, not the directory entry; `MOVEFILE_WRITE_THROUGH` issues no flush and no FUA; the directory-handle flush appeared to be the only positive operation (attribution revised by E17). |
| **E15** | Reproduce E14 on a full Windows stack | QEMU + full Windows 10 Pro 22H2 golden image; same arms | Identical results; the E14 findings are not a WinPE artifact. |
| **E16** | Documentation backfill (not an experiment) | — | Updated the librime comment, the leveldb PR #1383 body and the fork README with the E14/E15 results. |
| **E17** | Per-DB patch shapes; parent-directory flush vs volume-root flush | QEMU + WinPE 19041 and full Windows 10 Pro 22H2; 16 arms ×3 reps + installer arm 90 | Parent-directory flush: 0 MFT-zone writes, the entry is lost (also with a non-resident `$I30`); volume-root flush: 4–5 writes, create/rename/delete persist. Revises E14. |
| **E18** | Reserved: run the actual patch bodies through the rig | — | Never executed. |
| **E19** | Does `fsync(dir)` matter on Linux + NTFS? | Debian 12 + ntfs-3g, three NTFS volumes under `blklogwrites` | `fsync(file)` alone already persists the directory entry; `fsync(dir)` adds nothing; the no-flush teeth arm loses the file. Opposite of ext4. |
| **E20** | Windows + ext4 | — | Assessed as not executable: no supported driver, non-admin host, and a third-party FSD would not generalize. |
| **E21** | On a real Windows kernel, does a parent-directory flush reach the device? | GitHub Actions `windows-latest`, `wpr` DiskIO ETW; four cumulative arms ×100 reps | Flush IRPs +1.00/call for both the parent directory and the volume root; writes ~0.1/call for the parent vs 1.3–3.1 for the root. The parent-directory flush is an empty-set flush; reproduces E17. |
| **E22** | ntfs3 + `dm-log-writes` in CI | Ubuntu runner, ntfs3, `dm-log-writes` | Minimal repro runs, but the filesystem landed on the log image and the log device read zeros; inconclusive. Kernel source: `ntfs_file_fsync` includes parent-index writeback. |
| **E23** | Per-commit cost, SQLite-shaped simulation | GitHub Actions `windows-latest`; 200 transactions per variant + warmup | Directory flush free; root flush +1.473 ms/commit (≈2×, TPS 698→344). |
| **E24** | Real SQLite build cost | `windows-latest`; three amalgamation variants (base / dir / dir+root) | Directory flush +0.06 ms/txn; dir+root +5.4 ms/txn (≈5.2×, TPS 795→150); EXTRA 2×; WAL zero tax; speedtest1 +35–50%. |
| **E25** | Real LMDB build cost | `windows-latest` plus Linux jobs; three variants | Root flush +2.5–3 ms per fresh env (`env_open`/`env_copy`); commit path zero tax; root flush works non-admin on the runner (`root_err=0`). |
| **E26** (WITHDRAWN 2026-11-21: volume-root flush requires privileges, unusable non-elevated) | Patch-family cost matrix | GitHub Actions rigs for leveldb / restic / pebble / kit / RocksDB / MySQL; PostgreSQL on the school machine | leveldb +3.5 ms/manifest-sync at 256 KiB; restic +38 ms/backup (+4%); pebble +22–31 ms/flush growing with dirty L0; kit directory flush +43 µs/call, dir+root ≈4.5 ms/call; RocksDB 3.4–6.3× base; MySQL 1.3–1.6 ms/op; PostgreSQL 559 µs (C: root ACL denied); root cost tracks the volume's dirty metadata. |
| **E27** | Operation-level `FILE_WRITE_THROUGH`: mkdir / rename / delete on the operation's own handle | Proposed: local ETW first, then the QEMU crash rig | Not run. Highest-value arm is rename (the Microsoft documentation's example); the file-create analog is negative in E14 arm 5. |

## Detailed summaries

The same rigs in their original long-form entries:

| Rig | Question | Method | Result |
|---|---|---|---|
| **E11** (Linux QEMU power-cut model) | Does rename + directory `fsync` survive a power cut on a POSIX FS? | Linux guest (ext4), probe `e11-probe.c`: rename → optional `fsync(dir)` → host kills QEMU → reboot autopsy | sync arm 5/5 SURVIVED, nosync arm 8/8 LOST (`e11_v2.sh` fixed the v1 harness bug where unsynced binaries were judged). Demonstrates the mechanism discriminates; **Linux evidence, not Windows** |
| **E14/E15** (Windows block-command tracing) | On NTFS, which operation actually puts a namespace change on the device? | QEMU `blklogwrites` records every WRITE/FLUSH/FUA the guest issues; arm id via SMBIOS serial; the power-cut instant is an **offline cut** of the log (worst = up to the last barrier, best = everything the device received); ntfs-3g double-mount autopsy (ro,norecover = raw platter / rw = replayed). Ran on WinPE and full Windows 10 Pro 22H2, six arms, three reps each, identical results | `FlushFileBuffers(file)` covers data but not the directory entry; `MOVEFILE_WRITE_THROUGH` rename issues **no flush and no FUA**; the **directory-handle flush is the only observed operation that puts the namespace change on the device**. Full report: [E14-RESULTS.md](E14-RESULTS.md) |
| **E17** (per-DB shape verification, completed 2026-11-17) | Do the exact API sequences each database's Windows dir-flush patch uses (leveldb 30b34e0 / SQLite / LMDB / PostgreSQL / MySQL / FrankenSQLite) put the namespace change on the device — including **delete** and **mkdir**? | Same rig as E14, probe `e17pe.cpp` with 16 arms reproducing each patch's literal call sequence plus controlled disambiguation arms (flush parent dir vs flush volume root); error codes embedded in markers. **Re-tested on a full Windows 10 Pro 22H2 guest** (the E15 golden image, probe injected via a maintenance-boot installer arm) | **The five patches' shared shape — a single `FlushFileBuffers` on the parent directory — produced ZERO device-level metadata writes** (arms 1-5, 7-8, 13: file data barriered, namespace not on platter). The platter effect comes specifically from `FlushFileBuffers` on the **volume root** (arm 11: root flush alone persists the file; arm 13: parent-dir flush twice does nothing). Corrected protocols verified: rename + parent+root flush persists the dst (arm 14), durable delete + parent+root flush persists the deletion (arm 15). **All five key arms reproduce byte-for-byte on the full Windows stack** (same MFT records, same platter verdicts). **This revises E14's attribution**: E14's positive signal came from its pre-state's root flush, not from the parent-directory flush. Full report: [E17-RESULTS.md](E17-RESULTS.md) |
| **E19** (Linux + NTFS, completed 2026-11-17) | On Linux with ntfs-3g, does the POSIX rule "fsync the parent directory after create/rename" actually matter, or is it filesystem-specific? | E11 protocol on the E17 NTFS data disks: Debian 12 guest, three NTFS volumes under separate blklogwrites, ntfs-3g rw mounts; probe `e19lin.c` = fsync pre-state, create 2048B file, arm-specific flush; 1 s window then host kills QEMU; autopsy = 3 read-only originals + 3 rw copies, ro,norecover vs rw replay | **`fsync(file)` alone already persists the directory entry on ntfs-3g** (arms 0 and 1 identical: file on the platter); `fsync(dir)` adds nothing; the no-flush teeth arm loses the file (model discriminates). **Opposite of ext4** (E11: fsync(file) 8/8 lost, fsync(dir) required) — so "must fsync the parent dir" is a per-filesystem-driver contract, not a universal POSIX guarantee. Full report: [E19-RESULTS.md](E19-RESULTS.md); Windows+ext4 (E20) assessed as not executable (no supported driver, non-admin host, and a third-party FSD would not generalize) |
| **E21** (real Windows kernel + GitHub Actions, completed 2026-11-17) | On real hardware, does `FlushFileBuffers(parent dir)` reach the device at all - and does it make the namespace durable? | `run-tier1.ps1` + a workflow on `windows-latest`: four cumulative arms (no flush / flush file / + flush parent dir / + flush root), each in its own wpr DiskIO ETW session, **measurement window contains only the flush calls**, 100 reps per arm; counts `DiskIo/FlushBuffers` and `Write` events | **The parent-dir flush is not a no-op, but it flushes an empty set.** Flush IRPs per call: +1.00 for both the parent-dir and the volume-root flush (identical at the storage stack). Write events per call: **~0.1 for the parent dir vs ~1.3-3.1 for the volume root** (10x apart, four 100-rep runs). NTFS does not write the new file MFT record or directory index in response to a directory-handle flush, so the flush has nothing to make durable - which reconciles MS-FSA 2.1.5.7 <80> (the directory flush exists) with the QEMU result (it changes nothing). Full report: [E21-RESULTS.md](E21-RESULTS.md) |
| **E23** (per-commit cost, completed 2026-11-18) | What does each flush variant cost amortized per commit (SQLite pays once per transaction)? | `bench-sqlite-commit.ps1` + a workflow on `windows-latest` (volume D:): simulated rollback-journal commit sequence (create journal → 2 KiB non-resident write+fsync → db write+fsync → delete journal → variant flushes), 200 transactions per variant + 20 warmup | **dir flush is free** (−0.016 ms/commit, noise — cheap precisely because it does no work), **root flush +1.473 ms/commit** (≈2×, TPS 698→344) — the unit price of namespace durability on Azure NVMe. Leveldb amortizes one flush per 1–4 MB (acceptable); SQLite pays per transaction (B1 vs B2 decision). Only the deltas are the signal; absolute TPS is not (the sim omits SQLite parsing/B-tree/locking). Results: [E21-RESULTS.md](E21-RESULTS.md) (E23 section) |
| **E24/E25** (real-build costs, completed 2026-11-18) | What do the flushes cost on **real SQLite and real LMDB** builds, where each engine pays at a different rate? | E24: three SQLite amalgamation variants (base / dir / dir+root) built from the same source, commit-shaped bench + speedtest1 + the dirsyncprobe rig upgraded for the root flush (`-DEXPECT_ROOT_FLUSH`, S8 expectations computed from the resolved name) + a hook counter proving the hook fires. E25: three LMDB variants (pristine / dir / dir+root), env-creation and commit phases, windows+linux jobs, probe with `DIRSYNC-PROBE-ROOT` line | **E24 (SQLite, synchronous=FULL): dir flush free (+0.06 ms/txn), dir+root +5.4 ms/txn (≈5.2×, TPS 795→150)**; EXTRA doubles it exactly (2×5.3); WAL mode zero tax; speedtest1 mixed workload +35–50%. The real per-flush price (5.4 ms) brackets with E23's sim (1.47 ms) — device- and dirty-metadata-dependent. **E25 (LMDB): the patch fires at env_open/env_copy only — dir+root +2.5–3 ms per fresh env, commit path untouched (phase B equal across variants), root flush works non-admin on the runner (`root_err=0`), red side intact.** Report: [E24-RESULTS.md](E24-RESULTS.md) |
| **E26** (patch-family costs, completed 2026-11-19; **WITHDRAWN 2026-11-21 — root flush requires privileges, cost numbers moot**) | Same three-variant method on the rest of the family — leveldb, restic, pebble, kit, RocksDB (real builds), MySQL (call-site sim), PostgreSQL (school machine) — where does each engine pay the flush price? | Per-engine rigs on the patched repos' `bench-e26` branches (leveldb: DB::Put through three env_windows builds; restic: init+40 backups through three local_windows.go builds; pebble: 300 explicit Flush() through three vfs builds; kit: resilient_fs sync cycles through three backend builds; RocksDB: db_bench fillrandom × three WinDirectory::Fsync builds; MySQL: six-call-site Win32 sim; PG: `t_dirsync_root.c` + `t_rootcost.c` probes on Win11) | **leveldb: dir free, root +3.5 ms/manifest-sync (~1 ms/MB at defaults, 25 ms/MB at 64 KiB buffers)**; **restic: dir free, root +38 ms/backup (+4%)**; **pebble: dir free, root +22–31 ms/flush and growing with dirty L0**; **kit: upstream `sync_directory` errors outright on Windows (os error 1 ×200) — the patch family turns a failing API into a working one**; **RocksDB: dir free, root 3.4–6.3× base (13–15 µs/op on 128 KiB flushes)**; **kit: dir +43 µs/call; dirroot root flush ≈4.5 ms/call (head 8488ca5a, 200/200 clean; the superseded 669d3a4a variant reported 200/200 err32 — its root open lacked FILE_SHARE_DELETE)**; **MySQL sim: root 1.3–1.6 ms/op (matches E23)**; **PG/school machine: root open is EACCES-tolerated on C: (only FILE_APPEND_DATA opens non-elevated — kit-review finding reproduced), standalone root flush 100–280 ms on busy volumes — price = whatever the volume has dirty**. Report: [E26-RESULTS.md](E26-RESULTS.md) |

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

### E26 (patch-family costs; rigs live on each repo's `bench-e26` branch)
- E26a leveldb: `e26_lldb_bench.c` + `e26-leveldb.yml` on takomitkm/leveldb `bench-e26` (three env_windows builds; cl needs VsDevShell + `/MD` to match the cmake-built lib)
- E26b restic: `e26-restic.yml` on takomitkm/restic `bench-e26` (three restic builds, init+40 backups)
- E26c pebble: `e26-pebble.yml` + `pebblebench/main.go` on takomitkm/pebble `bench-e26` (bench copied into each checkout, `go run`)
- E26d kit: `e26-kit.yml` + `examples/e26bench.rs` on takomitkm/kit `bench-e26` (bench counts errors — upstream Windows sync_directory fails outright)
- E26e rocksdb: `e26-rocksdb.yml` on takomitkm/rocksdb `bench-e26` (matrix; needs `-DWARNINGS_AS_ERRORS=0` on the current runner image)
- E26f mysql: `e26-mysql-sim.yml` + `e26_mysql_sim.ps1` — runs on takomitkm/leveldb `bench-e26` (the mysql-server fork's Actions never fires newly added workflows)
- E26g PG: `t_dirsync_root.c` + `t_rootcost.c` in `.tmp/lvl-fb/pg-dirsync/`, run on the school machine (school-guest); v4 patch generator = `pg_patch_build_v3.py` (v4 output; byte-level escape discipline: `\0`/`\\` inside the Python triple-quoted C template must be `\\0`/`\\\\`)
- Results in [E26-RESULTS.md](E26-RESULTS.md)

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
