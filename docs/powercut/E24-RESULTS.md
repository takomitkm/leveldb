# E24/E25 — the flush costs measured on real builds (2026-11-18)

E23 simulated the SQLite commit sequence and measured the root flush at
+1.47 ms per commit. E24 builds **real SQLite** (three variants of the
amalgamation) and runs real transactions; E25 builds **real LMDB** the same
way. Together they answer "what does the volume-root flush actually cost per
transaction on each engine", which is the number SQLite needs before adding
anything to `os_win.c`.

## E24: real SQLite, three variants (takomitkm/sqlite, branch `bench`)

Three builds from the same amalgamation (3.50.x):

- `base` — vanilla SQLite; `syncDir` still discarded.
- `dir` — the dir-sync patch: `winSyncDirectory()` (parent-directory
  `FlushFileBuffers`) hooked into the journal's first `xSync` and into
  `xDelete(syncDir=1)`; this is the reviewed shape of the earlier patch.
- `dir+root` — the same patch plus the volume-root flush after the parent
  flush (`winSyncVolumeRoot`), i.e. the measured-effective shape.

Verification before the numbers: the `dirsyncprobe.c` red/green rig (now
`-DEXPECT_ROOT_FLUSH=1` aware, with the S8 drive-relative expectations
computed from the resolved name — the per-drive cwd starts at the volume
root, so a resolved name can sit directly in `X:\` where the parent flush IS
the root flush), plus a hook counter (`sqlite3_win_dir_sync_count`) that the
bench prints: `dir`/`dir+root` count exactly 1 per commit in FULL mode and 2
per commit in EXTRA mode (500 → 501/1002 observed), `base` stays 0. The
probe passes 4×green against the patched libraries and fails 11 checks
against the unpatched one (teeth intact).

### Commit-shaped bench (`e24_bench.c`, 500 txns × 3 interleaved rounds, runner volume D:)

| synchronous=FULL | ms/txn | TPS | delta vs base |
|---|---|---|---|
| base | 1.234–1.270 | 787–811 | — |
| dir | 1.296–1.364 | 733–772 | **+0.06 (noise-level)** |
| dir+root | 6.538–6.791 | 147–153 | **+5.4 ms/txn (≈5.2×)** |

| synchronous=EXTRA (2 dir syncs/txn) | ms/txn | delta |
|---|---|---|
| base | 1.193–1.221 | — |
| dir | 1.426–1.475 | +0.22 |
| dir+root | 11.707–12.811 | ≈ 2 × 5.3 ms — consistent with one root flush per dir sync |

### speedtest1 (`--size 25`, interleaved rounds)

- DELETE journal: base 1.87–2.11 s, dir 1.88–1.93 s, dir+root 2.47–2.89 s
  (+35–50% on this mixed workload — fewer commits per second than the
  insert-shaped bench, so the per-commit flush tax dilutes).
- nosync control: base and dir+root indistinguishable (1.82–2.37 s, noisy)
  — the cost is the flushes, nothing else.
- WAL journal: base 1.42–1.44 s, dir+root 1.39–1.41 s — **zero tax**, as
  designed: WAL mode never deletes a journal, so the hook does not fire.

### Why the real number is 5.4 ms and not E23's 1.47 ms

E23 flushed the volume root once per simulated commit with a plain
2 KiB-file create/delete pattern. Real SQLite in DELETE mode creates the
journal, fsyncs it, writes the db, fsyncs, deletes the journal — every one
of those dirties MFT/INDX metadata on the volume, so each root flush has
more writeback to push on this Azure-backed runner volume. The two
independent measurements bracket the per-flush price: 1.47 ms (E23 sim) to
5.4 ms (E24 real), and the EXTRA round doubles it exactly. **The honest
range to quote: a volume-root flush costs single-digit milliseconds on a
virtualized runner volume, device-dependent.**

Implication for the SQLite decision (B1 vs B2): B1 (add the root flush)
costs ≈2–5× commit latency in DELETE+synchronous=FULL mode and is free in
WAL mode; B2 (documentation-only) stays free. That trade-off is the
drh-facing number.

Run: takomitkm/sqlite `dirsync-bench` 44201372408 (green). Rig: `e24_root_patch.py`
(fail-closed variant generator), `e24_bench.c`, upgraded `dirsyncprobe.c`,
`bench.yml` on the `bench` branch.

## E25: real LMDB, three variants (takomitkm/lmdb, branch `dirflush`)

LMDB's directory flush (the mdb_mdirsync patch, +372 lines with the root
flush added) fires at **env_open and env_copy, not in the commit path** —
so the cost profile is completely different from SQLite's:

- Phase A (100 fresh env creations — the flush path):
  base 1.08–1.52 ms/env, dir 1.10–1.78, **dir+root 3.59–4.36** — the root
  flush adds ≈2.5–3 ms per newly created environment. This happens once per
  `mdb_env_open` on a fresh directory, not per transaction.
- Phase B (2000 commits in one env — must not differ): base/dir/dirroot all
  0.36–0.43 ms/txn, interleaved and equal. **The patch does not tax the
  commit path**, which is the discriminating control.
- Linux job (POSIX side, fsync on a directory fd via strace): all three
  variants equal in both phases — the root flush is Windows-only, and the
  POSIX dir fsync is already cheap (0.23–0.77 ms/env).
- Probe: `DIRSYNC-PROBE dir=... open_err=0 flush_err=0` ×2 and
  `DIRSYNC-PROBE-ROOT ... root_err=0` ×2 on the runner volume as a
  non-admin — the volume root opens and flushes without elevation, and the
  pristine build prints zero probe lines (red side intact).

Run: takomitkm/lmdb `dirflush` 44202019227 (windows + linux both green).
Rig: the patch (`mdb.c` + `mdb-base.c`/`mdb-dir.c`/`mdb-dirroot.c`),
`e25_lmdb_bench.c`, `dirsync-test.c`/`dirname-test.c`/`race-test.c`, and
`dirflush.yml` on the branch.

## Cross-engine cost picture (runner volumes, deltas only)

| Engine | Flush site | dir flush | root flush | Per-commit impact |
|---|---|---|---|---|
| SQLite (DELETE+FULL) | per txn (journal first-xSync) | free (+0.06) | 1×/txn | **+5.4 ms/txn (≈5×)** |
| SQLite (DELETE+EXTRA) | per txn ×2 | +0.22 | 2×/txn | ≈+11 ms/txn |
| SQLite (WAL) | once per WAL file | — | — | zero |
| LMDB | env_open / env_copy only | +0.2–0.4/env | 1×/env | +2.5–3 ms per fresh env; commits untouched |
| leveldb (E23) | per LogAndApply (1–4 MB) | free | 1× | ≈+1 ms/MB, acceptable |

The pattern is consistent: **the parent-directory flush is cheap because it
does no work; the volume-root flush costs single-digit ms because it does.**
Whether that price is worth paying depends entirely on how often the engine
pays it — per transaction (SQLite DELETE mode: expensive), per file
creation (LMDB: cheap), or per megabyte (leveldb: cheap).
