# Fuzz harnesses

Two standalone harnesses that stress the library beyond its unit tests. They
are not built by default; configure with `-DLEVELDB_BUILD_FUZZ_TESTS=ON`.

## `fuzz_db` - public-API fuzzing

Derived from Google's official OSS-Fuzz harness
([google/oss-fuzz `projects/leveldb/fuzz_db.cc`](https://github.com/google/oss-fuzz/blob/master/projects/leveldb/fuzz_db.cc),
Apache 2.0), with three changes: the missing `break` statements in the
`kIterate`/`kGetReleaseSnapshot` cases are fixed, the hard-coded `/tmp/testdb`
is replaced by a per-process temp directory, and the `FuzzedDataProvider`
dependency is replaced by a small built-in reader so the harness also builds
with MSVC and MinGW, not only clang+libFuzzer.

Exercises: `Put` / `Get` / `Delete` / `GetProperty` / iterator sweeps /
snapshot reads / close-and-reopen / `CompactRange` - driven by pseudo-random
op streams. Under ASan this is a lifetime/UAF hunt across the public surface.

Run: `leveldb_fuzz_db <iterations> [seed]` (defaults: 500 iterations, seed 1).
With clang you can also drive the same entry point through real libFuzzer by
compiling with `-fsanitize=fuzzer` and a `LLVMFuzzerTestOneInput` entry (the
file exposes one for that purpose).

## `fuzz_recovery` - corruption/recovery fuzzing

Nothing upstream fuzzes the *recovery* paths; this harness does, because this
fork patches several of them (durable `RenameFile`, #1334 short reads, #1336
incomplete-log handling, CRLF-tolerant `CURRENT`, the `RepairTable` parse
guard). For every seed it copies a small healthy database, flips 1–8 bytes at
seed-chosen offsets in a seed-chosen file (`.ldb` / `.log` / `CURRENT`,
sometimes CRLF-mangling `CURRENT` instead), then:

1. `DB::Open` - any result is fine as long as the process survives;
   if the open succeeds, a few `Get`s and an iterator sweep must work.
2. `RepairDB` + `DB::Open` again - same contract.

A crash, hang, or failed post-repair iteration on a repaired database is a
bug; `Corruption` statuses are expected and fine.

Run: `leveldb_fuzz_recovery <seeds> [seed]` (defaults: 200 seeds, seed 1).
