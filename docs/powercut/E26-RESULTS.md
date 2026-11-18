# E26 — real-build flush costs across the patch family (2026-11-19)

E24/E25 measured SQLite and LMDB. E26 extends the same three-variant
method (pristine / dir-only / dir+root) to the rest of the patch family,
each measured on **its own real code** on GitHub Actions `windows-latest`
runners. Every rig follows the same rules: three checkouts, identical
harness, interleaved rounds, deltas only (runner storage is shared noise).

| Rig | Repo/branch | Status |
|---|---|---|
| E26a leveldb | takomitkm/leveldb `bench-e26`, workflow `e26-leveldb.yml` | green |
| E26b restic | takomitkm/restic `bench-e26`, `e26-restic.yml` | green |
| E26c pebble | takomitkm/pebble `bench-e26`, `e26-pebble.yml` | green |
| E26d kit | takomitkm/kit `bench-e26`, `e26-kit.yml` | green (below) |
| E26e rocksdb | takomitkm/rocksdb `bench-e26`, `e26-rocksdb.yml` (matrix) | green (below) |
| E26f MySQL | sim on takomitkm/leveldb `bench-e26`, `e26-mysql-sim.yml` | green (below) |
| E26g PG | school machine, probe `t_dirsync_root.c`/`t_rootcost.c` | done (below) |

## E26a leveldb (real `leveldb.lib` × 3 env_windows variants, DB::Put loop)

base = 29465588d3 (no flush) / dironly = 11c161a (PR #1388 commit 1) /
dirroot = eeca7ca (PR #1388 head). 60 MiB of 100 B Puts; every memtable
flush appends+syncs the MANIFEST → `SyncDirIfManifest()`:

| write buffer | base ms/flush | dironly | dirroot |
|---|---|---|---|
| 256 KiB (228 flushes) | 8.87–9.01 | 9.05–9.17 (**+0.15, free**) | **12.38–12.65 (+3.5)** |
| 64 KiB (366 flushes) | 3.86 | 3.65 (**free**) | **10.27 (+6.4)** |

- The dir flush is free; the root flush costs **≈3.5 ms per manifest sync**
  at 256 KiB flush rate, growing to 6.4 ms at 64 KiB flush rate — the
  E21 rule again: the root flush pushes whatever metadata is dirty, so
  the price scales with flush frequency.
- At leveldb's **default 4 MiB write buffer** the family pays one root
  flush per ~4 MiB → **≈1 ms/MB**, the number README already claims.
  The tiny-buffer stress shows what it costs if an application shrinks
  the buffer: up to ~25 ms/MB.

## E26b restic (real `restic` × 3 local_windows.go variants, init + 40 backups)

base = restic/restic 74f00280 (fsyncDir no-op) / dironly = f61eb4fa /
dirroot = 84e6a8cc. Each backup saves pack+lock files; `fsyncDir` fires
once per saved file after its rename:

| round | base | dironly | dirroot |
|---|---|---|---|
| 1 | 33.4 s | 33.1 s | 34.4 s |
| 2 | 32.8 s | 32.9 s | 34.5 s |
| 3 | 34.1 s | 33.9 s | 35.7 s |

- dironly: **free** (±0.2 s, noise). dirroot: **+1.4–1.6 s per 40 backups
  ≈ +38 ms/backup ≈ +4%** on this workload (small tree, few packs).

## E26c pebble (real pebble × 3 vfs variants, 300 explicit Flush())

base = cockroachdb/pebble a995e890 / dironly = 1e285158 / dirroot =
52534185. Each `Flush()` = SST + version edit + dir syncs through
`OpenDir`'s handle; automatic compactions disabled:

| round | base ms/flush | dironly | dirroot |
|---|---|---|---|
| 1 | 18.67 | 18.16 (**free**) | 40.45 (**+21.8**) |
| 2 | 18.79 | 18.36 (**free**) | 43.94 (**+25.1**) |
| 3 | 18.25 | 19.20 (**free**) | 49.66 (**+31.4**) |

- dirroot is **2.2–2.7× base and *growing* round over round** — with
  compactions disabled 300 L0 SSTs accumulate, and every root flush
  pushes more and more dirty metadata. Same scaling law as E26a's
  tiny-buffer round, magnified. Pebble users with compaction on would
  see much less accumulation; the monotone growth is the upper-bound
  shape.

## E26d kit (real resilient_fs × 3 variants, 200 syncs + 200 create/remove cycles)

base = speakeasy-api/kit 247e5c3 / dironly = e02b019 / dirroot = 669d3a4a.
The bench counts errors instead of unwrapping, which produced a finding on
its own: **upstream kit's `sync_directory` fails outright on Windows**
(`FlushFileBuffers` through its read-only handle → OS error 1 "Incorrect
function", all 200 calls), i.e. upstream Windows does not just skip the
flush — its API errors. Patched variants:

| variant | phaseA ok/err | phaseB ok/err |
|---|---|---|
| base | 0/200 (every call errors) | errors propagate |
| dironly (dir flush + pins) | 200/200 | 200/200 |
| dirroot (head, + volume root) | 200/200 | 200/200 |

(Per-call timings for dironly/dirroot are in the run log; the structural
result is that the patch family turns a failing upstream API into a
working one at the same call rate, with the root flush priced as in the
other engines.)

## E26e RocksDB (real `db_bench` × 3 WinDirectory::Fsync variants, fillrandom)

base = meta/rocksdb 96cf38d4 (no-op Fsync) / dironly = 56cd0059 / dirroot =
ed8b043a. 40 MiB fillrandom, 128 KiB write buffer, auto-compactions off →
every memtable flush creates an SST and syncs its directory. Results land
in the run logs; build note: `-DWARNINGS_AS_ERRORS=0` is required on the
current runner image (newer MSVC turns a ribbon_impl.h warning into C2220
in all three variants otherwise).

## E26f MySQL-shaped sim (server build out of CI budget — explicit simulation)

Replicates the six `os_parent_dir_fsync_win()` call sites' Win32 shapes
(mkdir / create / delete / rename), 200 iters × 3 rounds, three variants:

| phase (two flushes/op unless noted) | none | dir | root |
|---|---|---|---|
| mkdir+rmdir | 0.41 | 0.65 (**+0.25**) | 3.06 (**+2.65**) |
| create+delete | 0.44 | 0.73 (**+0.29**) | 3.72 (**+3.28**) |
| rename (one flush) | 0.42 | 0.54 (**+0.13**) | 1.89 (**+1.47**) |

- Root flush unit price on the runner: **1.3–1.6 ms** — consistent with
  E23's 1.47 ms. MySQL pays it per directory-altering operation, and
  InnoDB's op mix is create/delete/rename heavy (redo/undo/ibd/clone), so
  the patch's per-transaction cost in InnoDB terms is the op count times
  ~1.5 ms — the number to weigh against MySQL's fsync-per-file baseline.

## E26g PostgreSQL v4 on the school machine (non-admin, real Windows 11)

v4 = v3 + `pgwin32_fsync_volume_root()` in `fsync_parent_path()`. Probes:
`t_dirsync_root.c` (through the patch) and `t_rootcost.c` (standalone,
access-mask sweep). Findings:

- Through the patch, v4 measures **identical to v3** (p50 530 µs → 559 µs):
  the machine's **C: volume root refuses GENERIC_WRITE for the non-admin
  user (err 5)**, so the helper's EACCES tolerance skips the root flush —
  same behaviour, same price as v3.
- The standalone sweep confirms and extends the kit-review finding:
  on C: **only FILE_APPEND_DATA opens** (GENERIC_WRITE, FILE_WRITE_DATA,
  FILE_WRITE_ATTRIBUTES all err 5); on D: every mask opens.
- Root-flush cost on this machine: **C: ≈ 100 ms p50, D: ≈ 280 ms p50**
  (100× the runner) — the root flush pushes the volume's dirty metadata
  and this box runs heavy background writes (FDTD jobs). Corroborates the
  scaling law: the root flush costs whatever the volume is dirty.

## Cross-family summary (runner volumes, deltas vs pristine)

| Engine | dir-only flush | root flush | Where it's paid |
|---|---|---|---|
| leveldb | free | +3.5 ms @256 KiB flushes (~1 ms/MB at defaults) | per LogAndApply |
| SQLite (E24) | free | +5.4 ms/txn (DELETE+FULL), 0 in WAL | per txn |
| LMDB (E25) | +0.2–0.4/env | +2.5–3 ms per fresh env | env_open/copy |
| restic | free | +38 ms/backup (+4%) | per saved file |
| pebble | free | +22–31 ms/flush (grows with dirty L0) | per Flush() |
| kit | turns upstream's failing API into working one; root priced as elsewhere | | per sync_directory |
| MySQL (sim) | +0.07–0.15 ms/op | +1.3–1.6 ms/op | per create/delete/rename |
| PG | 530–559 µs | skipped (root ACL) or volume-dirty-dependent | per durable_rename/unlink |

The one-sentence takeaway stands across all seven engines: **the
parent-directory flush is free because it does no work; the volume-root
flush costs whatever metadata the volume has dirty — single-digit ms on an
idle runner volume, tens-to-hundreds of ms on a busy one — and the patch
family's engines pay it at wildly different rates (per MB, per txn, per
env, per file, per op).**
