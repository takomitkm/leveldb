> **This is a personal fork of [google/leveldb](https://github.com/google/leveldb).**
>
> **Purpose:** collect and merge the Windows-related pull requests that have been
> sitting unreviewed upstream for years, plus data-loss fixes that had no
> proposal at all, so that Windows users get a single tree where they all apply
> together. **This is not an official leveldb release.** Upstream `master` is
> untouched; everything here is meant to be offered back upstream.
>
> **Windows download choice:** each release provides `-acp` packages for original
> Win32 narrow-code-page paths and `-utf8` packages for explicitly UTF-8 paths.
> Both retain the safety fixes. See the [compatibility guide](docs/COMPATIBILITY.md)
> for the build option, CRT/compiler requirements, codec support and behavioural
> differences before replacing an existing library.
>
> **Contents (all sources listed).** Split into four groups: Windows↔POSIX
> inconsistencies that are fixed here, inconsistencies that are still open
> upstream, everything else, and the tests/CI this fork added (table D). The
> second-to-last column of each table records what downstream projects do about
> the problem **without fixing upstream** — either an application-level "delete
> the database and rebuild" policy, or a private patch inside their own fork.
> The last column names the CI job that guards the change.
>
> **How to tell what is original here.** When the *Change* title is a link — e.g.
> [Durable `RenameFile`](https://github.com/takomitkm/leveldb/commit/3951ee8) — the row is this fork's own work and the link points at the
> introducing commit. Titles in plain bold came from someone else's PR or
> issue, cited in the *Source* cell. Table D collects the fork's own tests and
> CI work, each row linked the same way.
>
> ### A. Windows ↔ POSIX inconsistencies — fixed in this fork
>
> These are cases where `env_posix.cc` and `env_windows.cc` behave differently
> and the Windows side is the weaker one.
>
> | Change | POSIX side | Windows side (before) | Source | Downstream workaround (no upstream fix) | CI guard |
> |---|---|---|---|---|---|
> | **[Durable `RenameFile`](https://github.com/takomitkm/leveldb/commit/3951ee8)** | `Sync()` calls `SyncDirIfManifest()` so the manifest's directory entry is flushed | rename returned before being durable, while `DeleteObsoleteFiles()` pruned files from *in-memory* state → `CURRENT` could be left pointing at a deleted manifest | **[3951ee8](https://github.com/takomitkm/leveldb/commit/3951ee8)** — no prior proposal (written for this fork); relates to [#195](https://github.com/google/leveldb/issues/195), [#775](https://github.com/google/leveldb/issues/775) | **Chromium**: `leveldb_chrome.cc` `DeleteDB()` = `DestroyDB` + `base::DeletePathRecursively`, and its callers log "corrupted (and deleted) database" — i.e. delete and rebuild. **Syncthing / IPFS (kubo) / Prometheus / aptly**: documented "delete the DB and let it rebuild" after CURRENT/MANIFEST corruption. **oodrive/leveldb.net**: replaced `RenameFile` with `MoveFileW` + delete-then-retry (cures the #775 symptom, not durability). **Minecraft (leveldb-mcpe)**: `DeleteFileW` + `MoveFileExW(flags=0)` — no durability either. **Rime (librime)**: repair the db at `Open()` time ([rime/librime#1260](https://github.com/rime/librime/pull/1260), in review) — recovery, not prevention. | `win-quick`; `build-and-test` windows/msvc (`TestRenameFileDurableSemantics`, `TestRenameFileReplacesExisting`) |
> | **Short read handling** in `SequentialFile::Read` | loops until `n` bytes or EOF | a short `ReadFile` was treated as EOF, silently truncating WAL/MANIFEST reads | [PR #1334](https://github.com/google/leveldb/pull/1334) (open since 2026, data loss) | none found | `leveldb_tests` (all `build-and-test` cells) |
> | **Precise `NowMicros()`** | `clock_gettime` (µs) | coarse system timer | [PR #1366](https://github.com/google/leveldb/pull/1366) (open since 2026) | none found | `leveldb_tests` |
> | **[Appending `NewLogger`](https://github.com/takomitkm/leveldb/commit/7c242f8)** | `NewLogger()` opens with `O_APPEND \| O_CREAT` — the LOG file grows across opens | `NewLogger()` opened with `fopen(..., "wN")`, truncating the diagnostic LOG at every open and destroying the previous run's diagnostics | **[7c242f8](https://github.com/takomitkm/leveldb/commit/7c242f8)** — written for this fork (one-line `fopen` mode change to `"aN"`, keeping the non-inheritable `"N"` flag) | none needed (cosmetic; upstream still truncates) | `win-quick` (`LoggerAppendsAcrossOpens`) |
> | **[Parent-directory sync for manifests](https://github.com/takomitkm/leveldb/commit/30b34e0)** | `Sync()` → `SyncDirIfManifest()` opens the parent directory and fsyncs it **before** the manifest data lands | `Sync()` said "On Windows no need to sync parent directory" and only called `FlushFileBuffers` on the file — a power failure could lose a newly created manifest's directory entry even though its data was written, leaving `CURRENT` pointing at a file that no longer exists | **[30b34e0](https://github.com/takomitkm/leveldb/commit/30b34e0)** — written for this fork: `SyncDirIfManifest()` mirrors the POSIX order, opening the parent directory with `CreateFileW(GENERIC_WRITE, FILE_FLAG_BACKUP_SEMANTICS)` and flushing it with `FlushFileBuffers()` (verified empirically on NTFS as a non-admin user on two volumes; the handle needs `GENERIC_WRITE` — a read-only directory handle fails with `ERROR_ACCESS_DENIED` — and no administrator privilege is involved). No prior proposal exists upstream. | **RocksDB**: `WinDirectory::Fsync` is a no-op. **Chromium**: `SyncParent()` is compiled out on Windows. No downstream project flushes the directory. | `win-quick` (`ManifestSyncFlushesDirectory`) |
> | [Regression test `TestRenameFileReplacesExisting`](https://github.com/takomitkm/leveldb/commit/3951ee8) | — | covers the `ReplaceFile` / `ERROR_UNABLE_TO_REMOVE_REPLACED` case from [#775](https://github.com/google/leveldb/issues/775) | [3951ee8](https://github.com/takomitkm/leveldb/commit/3951ee8) | n/a | `win-quick` (`TestRenameFileReplacesExisting`) |
> | **[Chunked non-mmap reads for requests of 4 GiB and more](https://github.com/takomitkm/leveldb/commit/0a472d3)** | `pread()` takes a `size_t` and serves any length | the request length went straight into `ReadFile`'s `DWORD` parameter: a read of exactly 2^32 truncated to **zero** and reported an empty result for data that was actually there — a reachable short-read-after-corruption for legitimate large blocks, since `ReadBlock` sizes its request from the block handle. mmap-backed reads were unaffected | **[0a472d3](https://github.com/takomitkm/leveldb/commit/0a472d3)** — written for this fork after the post-merge review; requests are chunked at the `DWORD` limit with the previous short-read semantics (one `ReadFile` call still for everything below 4 GiB) | none found | `win-quick` (`env_windows_test` `HugeReadRequestServedInChunks`) |
>
> ### B. Windows ↔ POSIX inconsistencies — still open upstream (documented, not changed here)
>
> **None remaining.** Every inconsistency the audit found is either fixed in
> table A or on the "checked and found consistent" list. The parent-directory
> sync — the last entry this table ever held, and the Windows counterpart of
> [#195](https://github.com/google/leveldb/issues/195) — was fixed in this
> fork on 2026-11-04 (see table A). Upstream still has both gaps.
>
> **Checked and found consistent** (so nobody has to audit them again): file
> mapping (both use mmap — `CreateFileMappingW`/`MapViewOfFile` on Windows),
> `SleepForMicroseconds` (both `std::this_thread::sleep_for`), `GetChildren`
> (both non-recursive), `LockFile` (both exclusive — `LockFile` region locks vs `fcntl`),
> and handle inheritance (`NewLogger` already uses `"wN"`, and `CreateFileW`
> handles are not inheritable by default).
>
> ### C. Other fixes (not platform parity)
>
> | Change | Source | Upstream status | Downstream workaround (no upstream fix) | CI guard |
> |---|---|---|---|---|
> | UTF-8 → UTF-16 path conversion for all file APIs in `env_windows.cc` (Windows-only capability; POSIX paths are byte strings) | [PR #988](https://github.com/google/leveldb/pull/988) by the original author | open since 2022; **fixed here to compile with GCC** (passed `false` where `LPBOOL` was expected). **Note the contract change:** upstream's `...A` APIs interpret path bytes as the system ANSI code page, while this PR interprets them as UTF-8. Callers that hand over ACP-encoded paths — for example MSVC's `std::filesystem::path::string()`, which converts to the ANSI code page — must switch to `u8string()`, otherwise non-ASCII paths stop working. Callers can instead select the `-acp` package or set `LEVELDB_WINDOWS_UTF8_PATHS=OFF` to retain native-code-page paths without changing their calls. The UTF-8 package keeps the UTF-8 contract. Both variants retain the safety fixes. | **bitcoin-core/leveldb-subtree**: full ANSI→Wide conversion in its `bitcoin-fork`. **Minecraft (leveldb-mcpe)** and **oodrive/leveldb.net**: their own `env_win.cc` already use the `W` APIs. | `build-and-test` all cells (compiles); `win-quick` (`TestOpenOnRead`) |
> | Compiler warning flags for Windows builds | [PR #1214](https://github.com/google/leveldb/pull/1214) | open since 2024; **fixed here to apply to Clang only** (`-Wno-deprecated-declarations` makes MSVC `cl` fail with D8021) | none (build-only) | `build-and-test` windows/clang |
> | **Do not reuse a log that ends with an incomplete record** (platform-independent) | [PR #1336](https://github.com/google/leveldb/pull/1336) | open since 2026; data loss | none found; Chromium instead enables upstream's manifest-reuse feature | `leveldb_tests` |
> | Handle short reads in `PosixRandomAccessFile::Read` (the POSIX-side twin of #1334) | [PR #1368](https://github.com/google/leveldb/pull/1368) | open since 2026 | none found | `leveldb_tests` |
> | Initialize `file_size` in `CompactionState::Output` (uninitialized read / UB, platform-independent) | [bitcoin-core/leveldb-subtree@ad9b1c9](https://github.com/bitcoin-core/leveldb-subtree/commit/ad9b1c989380538bfb19c5c65df0d3f72d8ed62b) | not upstream; cherry-picked from Bitcoin Core's maintained fork | fixed **only** in bitcoin-core's fork (that is the source above) | `leveldb_tests` (Boltdb/bitcoin fork patch, no test upstream) |
> | **Reject overflowing block entry lengths in `DecodeEntry`** — the uint32 bounds check wrapped for crafted entries near 2 GiB and the iterator read out of bounds (platform-independent) | [PR #1350](https://github.com/google/leveldb/pull/1350) → issue [#1348](https://github.com/google/leveldb/issues/1348), open since 2026, found via the issue scan | not upstream; includes the `RejectsOverflowingEntryLengths` regression test | none found | `leveldb_tests` (`RejectsOverflowingEntryLengths`) |
> | **Validate block handles against the file size before `ReadBlock`** — a crafted footer/index requested an unbounded read (allocation DoS); index, metaindex, filter and data handles are now bounds-checked (platform-independent) | [PR #1326](https://github.com/google/leveldb/pull/1326) → issue [#1318](https://github.com/google/leveldb/issues/1318), open since 2026, found via the issue scan | not upstream; includes the `OversizedBlockHandle` proof-of-concept regression test | none found | `leveldb_tests` (`OversizedBlockHandle`) |
> | **Guard against internal keys shorter than 8 bytes** — `ExtractUserKey` underflowed to a near-`SIZE_MAX` slice on truncated keys in release builds; short keys are now rejected at `DecodeFrom` (platform-independent) | [PR #1359](https://github.com/google/leveldb/pull/1359) → issue [#103](https://github.com/google/leveldb/issues/103), open since 2017, found via the issue scan | not upstream | none found | `leveldb_tests` |
> | **Tolerate CRLF line endings in `CURRENT`** — a `\n` or `\r\n` (or lone `\r`) terminator is accepted and the `\r` is stripped before the manifest name is used; a `CURRENT` with no terminator is still rejected as a torn write | [mojang/leveldb#8](https://github.com/Mojang/leveldb/pull/8), merged into Minecraft Bedrock (2024-12) | not proposed upstream; the same defect was fixed independently **three** times downstream: Mojang's original leveldb-mcpe tree ([commit 52edeaab](https://github.com/Amulet-Team/leveldb-mcpe/commit/52edeaab), 2017), [Amulet-Team/leveldb-mcpe#6](https://github.com/Amulet-Team/leveldb-mcpe/pull/6) (2021), and mojang/leveldb#8 (2024). A Windows text tool that rewrites `CURRENT` with CRLF leaves a `\r` inside the manifest file name, and every later `DB::Open()` fails with `Corruption: CURRENT points to a non-existent file` | **Minecraft** and **Amulet (leveldb-mcpe)**: this exact fix, carried in their own forks; upstream has nothing | `leveldb_tests` (`CurrentFileTerminator.*`, added in [3806b66](https://github.com/takomitkm/leveldb/commit/3806b66)) |
> | **`RepairTable()` skips entries whose internal key cannot be parsed** — the metadata pass in `ScanTable()` already skipped unparsable keys, but the table-rebuilding copy loop did not, so unparseable entries could reach a rebuilt table | [Amulet-Team/leveldb-mcpe@52253f2f](https://github.com/Amulet-Team/leveldb-mcpe/commit/52253f2f) (from Mojang's original leveldb-mcpe tree, 2018, fixed for a real Bedrock production bug) | not proposed upstream | **Minecraft ecosystem**: this fix, carried in their own trees | `leveldb_tests`; `fuzz` (recovery harness) |
> | **[Scope the MSVC/GCC exception/RTTI settings to the leveldb target, and make both optional](https://github.com/takomitkm/leveldb/commit/aa0c122)** — the old code rewrote the global `CMAKE_CXX_FLAGS` with `string(REGEX REPLACE ...)`, so any project consuming leveldb via `add_subdirectory()` / `FetchContent()` had RTTI and exceptions stripped from **its own** targets and could not opt back in without patching leveldb. **This fork deviates from Mojang#16's implementation**: that PR applies the settings with directory-wide `add_compile_options()` / `add_definitions()`, which its own review thread questioned (rohunMS asked for `target_compile_options()`; the author answered that benchmarks/tests being affected is acceptable and merged) — and it does break the build: with the flags no longer inside `CMAKE_CXX_FLAGS`, the Kyoto Cabinet probe now *succeeds* where upstream's silently failed, so `db_bench_tree_db` actually gets compiled and its system headers (`kcutil.h`) require exceptions, failing the ubuntu/gcc jobs. Here the flags are `target_compile_options(leveldb PRIVATE ...)` (the reviewer's shape): **exceptions are on by default** so the library matches how consumers build against the public headers (`_HAS_EXCEPTIONS=1`); `-DLEVELDB_DISABLE_EXCEPTIONS=ON` opts out (and sets `_HAS_EXCEPTIONS=0` on MSVC — as a **directory-level** definition, because the STL headers are inlined into every TU here: scoping it to the library alone would leave the library's STL at 0 and the tests'/benchmarks' at 1, giving the same inline functions two definitions (ODR violation); a directory property does not reach `add_subdirectory()` consumers, so they stay untouched). **RTTI also follows the compiler default (on)** for the same reason: with `-fno-rtti` on the library alone its targets emit no `typeinfo` for polymorphic classes such as `leveldb::WritableFile`, while consumer targets compiled at the compiler default still reference it and the link fails (`undefined reference to typeinfo for leveldb::WritableFile`, reproduced with GCC 15.2 on Ubuntu); `-DLEVELDB_DISABLE_RTTI=ON` opts in to upstream's RTTI-off codegen, with the documented constraint that all consumers must disable RTTI as well. Verified on MSVC and MinGW in both profiles | [Mojang/leveldb#16](https://github.com/Mojang/leveldb/pull/16) by Yeabsira Mekonnen (Microsoft), merged into Minecraft Bedrock (2025-02-28); supersedes the author's earlier [Mojang/leveldb#14](https://github.com/Mojang/leveldb/pull/14) | not proposed upstream, although the exact complaint is upstream issue [#731](https://github.com/google/leveldb/issues/731) (open since 2019, 8 comments, **no pull request ever filed**). Turning RTTI off in 1.23 was also an ABI break with no soname bump: it broke Ceph (undefined `typeinfo` references), plyvel, and forced Fedora to carry a patch — see [#731](https://github.com/google/leveldb/issues/731) and [#927](https://github.com/google/leveldb/issues/927). Note: upstream never noticed its own kyotocabinet/benchmark incompatibility because the global flags made the detection probe fail, silently skipping `db_bench_tree_db` | **Mojang/Minecraft** (source of this fix), **Ceph**, **plyvel**, and every distro packaging leveldb: each carries its own patch or build flag | `no-exceptions` job; `build-and-test` all cells (RTTI default pinned in [18b79bb](https://github.com/takomitkm/leveldb/commit/18b79bb)) |
> | **[Restore the original Windows path-code-page contract as a build option](https://github.com/takomitkm/leveldb/commit/6b5edb6)** — `LEVELDB_WINDOWS_UTF8_PATHS=OFF` selects the original narrow file API encoding; ON retains UTF-8. Logger and temporary-directory paths follow the same selection | written for this fork | not proposed upstream; preserves caller path bytes without removing the safety fixes | choose the `-acp` binary or the source-build option | `compatibility` (both path modes, calibrated mutations); `release` (extracted-package consumer) |
> | **[Honor custom sequential-file buffers during log recovery](https://github.com/takomitkm/leveldb/commit/6b5edb6)** — preserve each returned slice before another short read can overwrite a reusable external buffer | written for this fork; fixes a regression in the short-read integration | not proposed upstream; public `SequentialFile::Read` explicitly permits a result outside scratch | no caller workaround needed | `compatibility` (`ReadFromReusedBuffer`, `ShortReadsFromReusedBuffer`, calibrated mutation); full test suites |
> | **Reject a `UINT64_MAX` `next_file_number` recovered from a MANIFEST** — the sentinel value is a corruption marker; recovery computed `next + 1`, which wrapped the counter to zero, so subsequent files were numbered 0, 1, 2, … and collided with the numbers of live tables and logs (platform-independent) | [PR #1377](https://github.com/google/leveldb/pull/1377), open since 2026 | not upstream | none found | `leveldb_tests` (`RecoveryTest.ManifestNextFileNumberOverflow`) |
> | **Bound the decompression path before allocating** — the declared decompressed size is sanity-checked (sentinels, `SIZE_MAX` representability), large declarations are probed with a bounded streaming pass before the output allocation, and that allocation is `nothrow` (platform-independent) | [PR #1354](https://github.com/google/leveldb/pull/1354), open since 2026; the upstream hardening commits were cherry-picked into this fork as [652e9a7](https://github.com/takomitkm/leveldb/commit/652e9a7cbe61515321c28d3d615a8f2678eeef20) and [8f4d6f7](https://github.com/takomitkm/leveldb/commit/8f4d6f7e56c8044e226f5fab638bbef04e361f99) with their original git authorship kept (the hardening commit’s author is [fredlday-oss](https://github.com/fredlday-oss) <243643093+fredlday-oss@users.noreply.github.com>, carried over from the upstream re-authoring [186a123](https://github.com/google/leveldb/commit/186a1237210efdd7a801fa6614e8478efb2e370e); the initial push had used a placeholder address) | not upstream; includes the `ReadBlockIssue1349Test` suite | none found | `leveldb_tests`; `optional-deps` (the only job where the zstd cases execute — the regular cells have no zstd and skip them — and the job fails if any of them skips or the suite shrinks below 12) |
> | **Reject blocks whose restart offset exceeds 32 bits** — the decoded block's restart array must end inside the `uint32_t` range the format stores, otherwise the offset truncated and iterators read out of bounds (platform-independent) | [PR #1367](https://github.com/google/leveldb/pull/1367), open since 2026; fixes the >4 GiB data loss family | not upstream | none found | `leveldb_tests` |
> | **Reject block handles that cannot represent their raw read** — sizes that overflow `size_t` or wrap with the 5-byte trailer are rejected before any arithmetic result is used (platform-independent) | [PR #1380](https://github.com/google/leveldb/pull/1380), open since 2026 | not upstream | none found | `leveldb_tests` (`OversizedBlockHandle`, `ReadBlockIssue1349Test`) |
> | **Reject varints whose last byte overflows the decoded value** — `80 80 80 80 10` used to decode as 0 instead of failing (platform-independent) | [PR #1382](https://github.com/google/leveldb/pull/1382), open since 2026 | not upstream; legal redundant encodings (`80 00`, …) are still accepted | none found | `leveldb_tests` (`Coding.Varint32OverflowInLastByte`, `Coding.Varint64OverflowInLastByte`) |
> | **Drop tables with zero parseable keys during repair** — such a table is archived into `lost/` instead of being published with empty metadata (platform-independent) | [PR #1344](https://github.com/google/leveldb/pull/1344), open since 2026 | not upstream | none found | `leveldb_tests`; `fuzz` (recovery harness) |
> | **Flush the pending data block before applying `ChangeOptions`** — the open block was previously finished under the new settings, tripping the block-builder assert (platform-independent) | [PR #1340](https://github.com/google/leveldb/pull/1340), open since 2026 | not upstream | none found | `leveldb_tests` (`TableTest.ChangeOptions*`, added by the follow-up below) |
> | **Reject invalid internal keys when decoding MANIFEST edits** — `InternalKey::DecodeFrom` now validates the whole key (length *and* value type), the comparator orders undersized keys by length instead of decoding an out-of-bounds trailer, and `BuildTable`/compaction reject iterators producing malformed keys (platform-independent) | [PR #1335](https://github.com/google/leveldb/pull/1335), open since 2026 | not upstream; the error paths of the rejection were fixed in the follow-up below ([bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e)) | none found | `leveldb_tests` (`VersionEditTest.RejectShortInternalKeyInNewFile`, `dbformat_test`, `BuilderTest`) |
> | **[Fail compaction and `BuildTable` on invalid internal keys instead of dropping them](https://github.com/takomitkm/leveldb/commit/bf7212e)** — the tightened validation above shipped with two broken error paths: compaction skipped unparsable records *without* recording an error and could still succeed (silently destroying the input records and the evidence of what was wrong), and `BuildTable`'s rejection paths deleted the builder without `Abandon()`, aborting on the destructor assert instead of returning the corruption status. Compaction now fails with `Corruption` (input files stay referenced), `BuildTable` validates the first key before creating anything and routes interior rejections through one cleanup, and `GetInternalKey` delegates to `DecodeFrom` so the rules live in one place | written for this fork; follow-up to the #1335 backport ([72be98c](https://github.com/takomitkm/leveldb/commit/72be98c)), found by the 2026-11-07 per-commit review | not upstream | none found | `leveldb_tests` (`CorruptionTest.CompactionFailsOnInvalidInternalKey`, `BuilderTest.*`) |
> | **[Reject invalid `ChangeOptions` calls and surface flush failures](https://github.com/takomitkm/leveldb/commit/9789dab)** — the newly added flush (above) gave `ChangeOptions` two unhandled error paths: after `Finish()`/`Abandon()` it still touched the (possibly destroyed) file, and an I/O error it raised itself was stored in the sticky status while the call returned OK with the options swapped. `ChangeOptions` now rejects closed builders and filter-policy/`block_restart_interval` changes, returns its own flush errors, and leaves the options untouched on any failure — which also fixes the pre-existing dynamic `filter_policy` swap (a `Finish()` null dereference) and non-positive restart intervals | written for this fork; follow-up to the #1340 backport ([71c67e7](https://github.com/takomitkm/leveldb/commit/71c67e7)); the policy/interval gaps are baseline defects | not upstream | none found | `leveldb_tests` (`TableTest.ChangeOptions*`) |
> | **[Archive empty tables under the file name that is actually there](https://github.com/takomitkm/leveldb/commit/0959c2f)** — `ScanTable()` locates a table through a `.ldb`-first/`.sst`-fallback probe, but the empty-table branch above re-generated the `.ldb` name for archiving; for an old `.sst` table the rename silently failed, the original stayed behind, was excluded from the rebuilt MANIFEST, and the next `Open()` deleted it — losing the offline evidence | written for this fork; follow-up to the #1344 backport ([01b0196](https://github.com/takomitkm/leveldb/commit/01b0196)), reproduced end-to-end | not upstream | none found | `leveldb_tests` (`RepairArchivesEmptyTableUnderActualName`) |
> | **[Recompute rebuilt-table metadata from the records actually kept](https://github.com/takomitkm/leveldb/commit/4b6a977)** — `ScanTable()` that fails with a transient I/O error hands the file to `RepairTable()` with empty boundaries and sequence zero; publishing those wrote an empty `smallest` key (encode assert in debug, corrupt MANIFEST boundary in release) and recovered `LastSequence=0`, so the next write re-used sequence numbers and lost to the repaired record | written for this fork; baseline defect, found by the 2026-11-07 review | not upstream | none found | `leveldb_tests` (`RepairTableRecomputesMetadataAfterTransientError`) |
> | **[Make the Zstd pre-validation probe accept what one-pass decoding accepts](https://github.com/takomitkm/leveldb/commit/edba255)** — the streaming probe inherited zstd's default 2^27 window limit (checked against the *declared* window), so a frame declaring a 2^28 window with a 9 MiB payload — accepted by one-pass decoding, which officially ignores the window — was reported corrupt before producing a byte, and a frame ending with input left over rejected legal frame sequences such as a trailing zero-payload skippable frame. The probe now raises `ZSTD_d_windowLogMax` to 30 (covering every window a real encoder declares), continues with the next frame in the same context, and requires the accumulated output to equal the declared size. Measured with zstd 1.5.7 | written for this fork; follow-up to the #1354 backport ([719f24b](https://github.com/takomitkm/leveldb/commit/719f24b)), with the window policy chosen from a measured accept matrix | not upstream | none found | `leveldb_tests`; `optional-deps` (the only job where the zstd `ReadBlockIssue1349Test` cases execute, guarded to run rather than skip) |
> | **Report the raw block buffer allocation failure instead of throwing** — the initial read buffer used throwing `new`, so `std::bad_alloc` could escape through a `Status`-returning interface, inconsistent with the decompression buffers in the same function | written for this fork; follow-up to the #1354 backport | not upstream | none found | `leveldb_tests` (`ReportsAllocationFailureForHugeRawRead`) |
>
> **How the two path encodings coexist today.** With upstream's `...A` APIs the
> round trip is consistent as long as callers also hand over ANSI-code-page
> bytes: MSVC's `std::filesystem::path::string()` converts the internal UTF-16 to
> the system code page (e.g. GBK on a Chinese system), and `CreateFileA` reads
> those bytes back as the same code page. That is why an application can work on
> a Chinese Windows install without #988 — until a path contains a character the
> ANSI code page cannot represent (emoji, some rare ideographs), where
> `path::string()` throws or drops data. Content is unaffected either way: keys
> and values, including CJK text, are opaque bytes to leveldb and never go
> through a code-page conversion.
>
> **Why the durability fix matters:** on Windows, `RenameFile` returned before the
> rename was durable, and `DeleteObsoleteFiles()` decides what to keep from
> *in-memory* state rather than from the on-disk `CURRENT`. A crash — or a
> transient rename failure such as antivirus holding the file open — can
> therefore leave `CURRENT` pointing at a manifest that was already deleted, and
> every later `DB::Open()` fails with `Corruption: CURRENT points to a
> non-existent file`. `MOVEFILE_WRITE_THROUGH` makes the rename durable before
> returning, closing that window. The Windows port also skips parent-directory
> syncing by design (`env_windows.cc`, "On Windows no need to sync parent
> directory"), which is the Windows counterpart of #195.
>
> **Verified:** builds and passes its own test suite (`leveldb_tests`, `c_test`,
> `env_windows_test`) with **MSVC 19.44** and **MinGW-w64 GCC 16.2** on Windows 11
> x64 — 3/3 tests each. The rename test, the CRLF test (`CurrentFileWithCrlf`),
> the logger test (`LoggerAppendsAcrossOpens`) and the manifest-sync test
> (`ManifestSyncFlushesDirectory`) pass on both toolchains.
> The opt-in fuzz harnesses in [`fuzz/`](fuzz/README.md) (public API and
> recovery/corruption paths) also pass on both toolchains, and the whole
> suite passes under AddressSanitizer (MSVC, `/MT` — the `/MD` runtime
> miscompiles `std::mutex` under ASan). Verified 2026-11-04 (UTC+8).
>
> **Verified 2026-11-07 (UTC+8):** the 2026-11-07 wave (eight backports, six
> follow-up fixes, boundary tests) passes the full suite — `leveldb_tests`
> with 263 cases, `c_test`, `env_windows_test` — on MinGW-w64 GCC 16.2 Debug
> with Snappy and Zstd enabled, in a fresh out-of-tree build. Each fix in the
> wave was additionally verified **red** against its pre-fix parent commit
> before landing (the regression tests of table C fail on the parent and pass
> here), and the fork's CI matrix is green on
> [07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8).
>
> **RTTI default changed 2026-11-05:** `LEVELDB_DISABLE_RTTI` now defaults to
> **OFF**. The default build compiles the library with the compiler's own RTTI
> setting (enabled), matching how consumer targets are compiled — including
> this repo's own `leveldbutil`, `db_bench*` and test executables, which keep
> the compiler default because the no-RTTI flag is target-scoped
> (`target_compile_options(leveldb PRIVATE ...)`) and cannot propagate to
> them. With the previous default (ON), the library was built `-fno-rtti` and
> emitted no `typeinfo` for polymorphic classes (`leveldb::WritableFile`,
> `leveldb::Comparator`, ...), so consumer targets failed to link:
> `undefined reference to typeinfo for leveldb::WritableFile` (reproduced with
> GCC 15.2 on Ubuntu). Making the flag opt-in follows the
> `ROCKSDB_DISABLE_RTTI` convention and answers the demand of upstream
> [#731](https://github.com/google/leveldb/issues/731) (the 1.23 global
> RTTI-off ABI break broke Ceph and plyvel and forced distros to carry a
> patch — see also [#927](https://github.com/google/leveldb/issues/927)). To
> build without RTTI, pass `-DLEVELDB_DISABLE_RTTI=ON` **and** build all
> consumers with RTTI disabled as well (e.g.
> `-DCMAKE_CXX_FLAGS=-fno-rtti`); with only the library RTTI-off the link
> fails by design, and the CI matrix pins both modes explicitly.
>
> **Not included** (kept for the record):
> [#755](https://github.com/google/leveldb/pull/755) (empty patch) and
> [#760](https://github.com/google/leveldb/pull/760) (conflicts with current
> code). [#850](https://github.com/google/leveldb/pull/850) is already fixed
> upstream — `NewLogger()` opens with `fopen(..., "wN")`, which disables handle
> inheritance. Context: [#526](https://github.com/google/leveldb/pull/526), the
> earlier "comprehensive native Windows support" PR, was closed in 2019 when
> Google landed its own Windows port based on Chrome's code.
>
> ### D. Tests and CI added by this fork (2026-11-05, extended 2026-11-07)

> Everything below is original here — no upstream PR proposed it. Each row links
> the introducing commit and the CI job that now guards it, so a claim can be
> traced to code and to the check that keeps it true.

> | Change | Commit | CI guard | Why it exists |
> |---|---|---|---|
> | **[Parameterized `CURRENT` terminator matrix](https://github.com/takomitkm/leveldb/commit/3806b66)** — `TEST_P` over `LF` / `CRLF` / lone `CR` (accepted, terminator stripped before the manifest name is used) plus no-terminator / empty (rejected, and the database must survive the rejection) | [3806b66](https://github.com/takomitkm/leveldb/commit/3806b66) | `leveldb_tests` (`CurrentFileTerminator.*`) | The lone-`CR` case had zero coverage anywhere. Reverting `VersionSet::Recover()` to the upstream logic makes the `CRLF` and `CR` cases fail with the historically reported corruption messages (2 red / 3 green), so the matrix is calibrated rather than decorative |
> | **[`TestRenameFileDurableSemantics`](https://github.com/takomitkm/leveldb/commit/9418880)** — read-back through an independent `CreateFileW` handle after rename, replace-in-place case, independent directory enumeration, not-found error path | [9418880](https://github.com/takomitkm/leveldb/commit/9418880) | `win-quick`; `build-and-test` windows/msvc | `TestRenameFileReplacesExisting` covered the #775 replace surface only; the durable/replace split was untested. `MOVEFILE_WRITE_THROUGH` persistence is recorded as deliberately untested (needs power-loss injection) and backed by the `MoveFileExW` contract |
> | **[`win-quick` cell](https://github.com/takomitkm/leveldb/commit/6b2405e)** — the Windows-only suites (4 `EnvWindowsTest` cases plus `FileNameTest`/`Logging`) in their own job, every step asserting a minimum passed-count | [6b2405e](https://github.com/takomitkm/leveldb/commit/6b2405e) | itself | Windows regressions used to surface only after a full matrix round-trip (~15 min); now ~3 min. `ctest -R` exits 0 even when its regex matches nothing (verified on cmake 4.2.3), so a silent empty selection would otherwise look green |
> | **[MSVC cells build with the static CRT (`/MT`)](https://github.com/takomitkm/leveldb/commit/86e42d4)** | [86e42d4](https://github.com/takomitkm/leveldb/commit/86e42d4) | `build-and-test` windows/msvc; `win-quick` | Under `/MD`, a host whose `MSVCP140.dll` predates the toolchain crashes the test binaries in the fixture constructor (`0xC0000005`) before a single case runs; `/MT` removes that environment dependency (219/219 green on such a host) |
> | **[Fuzz regression-seed replay + crash-report artifact](https://github.com/takomitkm/leveldb/commit/a52543f)** | [a52543f](https://github.com/takomitkm/leveldb/commit/a52543f) | `fuzz` | Findings used to die in uploaded artifacts. `fuzz/regression_seeds.txt` is now replayed before every exploration run, so a fixed crash cannot silently return; failures ship a ready-to-edit seed line |
> | **[`LEVELDB_DISABLE_RTTI` defaults to OFF](https://github.com/takomitkm/leveldb/commit/18b79bb)** | [18b79bb](https://github.com/takomitkm/leveldb/commit/18b79bb) | `build-and-test` all cells | With it ON, the library emits no `typeinfo` for polymorphic classes while consumer targets keep the compiler default, so linking fails (`typeinfo for leveldb::WritableFile`). Opt-in now, mirroring `ROCKSDB_DISABLE_RTTI` |
> | **[Exceptions opt-out; flags scoped to the leveldb target](https://github.com/takomitkm/leveldb/commit/aa0c122)** | [aa0c122](https://github.com/takomitkm/leveldb/commit/aa0c122) | `no-exceptions` job; `build-and-test` all cells | Upstream rewrote the global `CMAKE_CXX_FLAGS`, stripping RTTI/exceptions from every consumer target (upstream [#731](https://github.com/google/leveldb/issues/731)). Target-scoped flags fix that without breaking the Kyoto Cabinet benchmark |
> | **Docs-only pushes and PRs skip CI** | [887fee5](https://github.com/takomitkm/leveldb/commit/887fee5) (push side), plus this commit (pull-request side) | all workflows | README/provenance edits were spending the full matrix; `paths-ignore` now covers `**.md`, `doc/**`, `LICENSE`, `AUTHORS` on both `push` and `pull_request` (see the note about required status checks in the workflow files) |
> | **[Probe `ZSTD_compress`, and test the decompression path](https://github.com/takomitkm/leveldb/commit/a34d62a)** — the optional-backend probes were only ever checked by their consumers; `CompressionTableTest.RoundTripThroughDecompression` reads every value back through the block reader, and the new `optional-deps` job asserts all four `HAVE_*` probes, the linkage, and that the tests ran instead of skipping | [a34d62a](https://github.com/takomitkm/leveldb/commit/a34d62a) | `optional-deps` (Debug/Release, both codecs required) | `check_library_exists(zstd zstd_compress …)` linked a symbol libzstd never exported (it is `ZSTD_compress`), so `HAVE_ZSTD` stayed empty even with `libzstd-dev` installed — the zstd code path was never compiled, and the only test covering it `GTEST_SKIP`ped silently, keeping CI green (upstream `bb74ef7` has the same defect, so the unoverridden CMake probe also failed in that snapshot). The new test is calibrated: forcing `Zstd_Uncompress()` to return false makes the zstd parameter fail with "corrupted zstd compressed block contents" while the old offset-only test stays green. `GTEST_SKIP` exits 0, so the job fails on a skip rather than trusting the green |
> | **[Path and custom-Env compatibility regressions](https://github.com/takomitkm/leveldb/commit/6b5edb6)** | [6b5edb6](https://github.com/takomitkm/leveldb/commit/6b5edb6) | `compatibility` | Non-ASCII file/logger/database paths are checked in ACP and UTF-8 modes; custom log readers reuse external buffers across full and short reads. Each old behaviour is restored temporarily to prove the new cases fail, then the fix is rebuilt and verified again |
> | **[Actions-built binary releases and extracted-package consumers](https://github.com/takomitkm/leveldb/commit/b57269c)** — 22 binary variants, including both Windows path modes, installed and archived with exact-source build records | [b57269c](https://github.com/takomitkm/leveldb/commit/b57269c) | `release` | Packages are checked for all public headers, relocatable CMake exports, runtime/dependency settings and working consumers built from the final extracted archives. Tag pushes publish only after all groups and manifests pass validation |
> | **[Per-fix regression tests for the 2026-11-07 wave](https://github.com/takomitkm/leveldb/commit/bf7212e)** — every fix in the table-C rows above ships with its own regression test (`BuilderTest` is a new suite registered in CMakeLists; the corruption, `ChangeOptions`, `ReadBlockIssue1349` and repair cases are named in each row's CI-guard cell), and each was verified red against the pre-fix commit before the fix landed | [bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e) through [527720c](https://github.com/takomitkm/leveldb/commit/527720c) | `leveldb_tests`; `win-quick` (`env_windows_test`); `optional-deps` | The tests exist because the whole wave passed the inherited suite green while carrying real defects — the suite knew nothing about any of these paths. Two details were only caught dynamically: the dangling `DBImpl*` in a first draft of the compaction test (debug SIGSEGV under gdb) and `ReadFile`'s kernel buffer probe (a short scratch with a 4 GiB request fails even when the fix is correct, so the test commits the contract-mandated 4 GiB scratch) |
> | **[Read-path boundary contracts pinned](https://github.com/takomitkm/leveldb/commit/07e88e8)** — failed varint decodes leave input and output untouched, successful ones advance exactly past the encoding, redundant-continuation encodings stay accepted; `SIZE_MAX` handle sizes are rejected as wrapping and the largest non-wrapping size goes through the allocation-failure path | [07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8) | `leveldb_tests` (`Coding.Varint*`, `ReadBlockIssue1349Test.RejectsHandleSizeThatWrapsWithTrailer`, `…RejectsLargestNonWrappingHandleSize`) | The three read-path guards ([aa3b900](https://github.com/takomitkm/leveldb/commit/aa3b900), [fb5dc12](https://github.com/takomitkm/leveldb/commit/fb5dc12), [7d10a9a](https://github.com/takomitkm/leveldb/commit/7d10a9a)) shipped without targeted tests. The restart-offset boundary was closed separately — see the next row |
> | **[The 4 GiB restart-offset boundary, closed with a real 4 GiB block](https://github.com/takomitkm/leveldb/commit/8c226e0)** — both boundary blocks built on one lazily-committed 4 GiB buffer (size U+5 with zero restarts → offset U+1 must come back `Corruption`; size U+4 → offset exactly U must stay accepted), judged by `NewIterator`'s status only: the uninitialized body is never iterated, so the test touches two pages and skips gracefully if the 4 GiB commit is unavailable | [8c226e0](https://github.com/takomitkm/leveldb/commit/8c226e0) | `leveldb_tests` (`BlockTest.RestartOffsetMustBeUint32Representable`) | Closes the gap the row above left open. Verified red against aa3b900's parent ([719f24b](https://github.com/takomitkm/leveldb/commit/719f24b): the offset truncated to 0 and the oversized block was accepted silently) and green here; note `NumRestarts() == 0` routes `NewIterator` to the empty iterator, which is what makes the two outcomes observable without decoding anything |

> ### Provenance, method, and scope — PR scan 2026-11-04 00:17, branch scan 2026-11-04 01:13 (UTC+8)
>
> **Every patch in this tree comes from one of these places, and nothing else:**
> the six open google/leveldb PRs named in the tables (#988, #1214, #1366,
> #1334, #1336, #1368), one commit from bitcoin-core/leveldb-subtree
> (`ad9b1c9`, the `file_size` initialization), two commits from Mojang's
> Minecraft leveldb trees ([mojang/leveldb#8](https://github.com/Mojang/leveldb/pull/8)
> — the CRLF fix — and [Amulet-Team/leveldb-mcpe@52253f2f](https://github.com/Amulet-Team/leveldb-mcpe/commit/52253f2f)
> — the `RepairTable` parse guard), the OSS-Fuzz public-API harness adapted
> into [`fuzz/fuzz_db.cc`](fuzz/fuzz_db.cc) (from
> [google/oss-fuzz](https://github.com/google/oss-fuzz/blob/master/projects/leveldb/fuzz_db.cc)),
> six things written for this fork
> because no prior proposal exists anywhere: the durable `RenameFile`, the
> appending `NewLogger`, the manifest parent-directory sync, the recovery
> fuzzer `fuzz/fuzz_recovery.cc`, the
> four regression tests, and this README audit — plus hardening fixes
> adopted from unmerged PRs found through the issue scans: three from the
> first pass ([#1350](https://github.com/google/leveldb/pull/1350),
> [#1326](https://github.com/google/leveldb/pull/1326), [#1359](https://github.com/google/leveldb/pull/1359))
> and eight more from the 2026-11-07 sweep ([#1335](https://github.com/google/leveldb/pull/1335),
> [#1340](https://github.com/google/leveldb/pull/1340), [#1344](https://github.com/google/leveldb/pull/1344),
> [#1354](https://github.com/google/leveldb/pull/1354), [#1367](https://github.com/google/leveldb/pull/1367),
> [#1377](https://github.com/google/leveldb/pull/1377), [#1380](https://github.com/google/leveldb/pull/1380),
> [#1382](https://github.com/google/leveldb/pull/1382), all in table C).
>
> **Post-merge review (2026-11-07):** the eight backports above were audited
> one commit at a time (independent read-only reviews plus a cross-commit
> integration pass), which found error-path defects in four of them and four
> baseline defects nearby. Six follow-up fixes were written for this fork
> ([bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e),
> [9789dab](https://github.com/takomitkm/leveldb/commit/9789dab),
> [0959c2f](https://github.com/takomitkm/leveldb/commit/0959c2f),
> [4b6a977](https://github.com/takomitkm/leveldb/commit/4b6a977),
> [edba255](https://github.com/takomitkm/leveldb/commit/edba255),
> [527720c](https://github.com/takomitkm/leveldb/commit/527720c), table C)
> and one platform-parity fix ([0a472d3](https://github.com/takomitkm/leveldb/commit/0a472d3),
> table A); every one was verified to fail red against its pre-fix parent
> before landing. Three of the backports keep their implementations unchanged
> and got boundary-contract tests instead ([07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8),
> table D); the restart-offset boundary test was later closed with a real
> 4 GiB block ([8c226e0](https://github.com/takomitkm/leveldb/commit/8c226e0),
> table D), so every guard in the wave now has targeted coverage.
>
> **Isolated verification against the upstream PR branches (2026-11-08):**
> the four follow-up fixes above were re-verified on the community PRs'
> own code, in isolation from this fork's other changes — for each of
> [#1335](https://github.com/google/leveldb/pull/1335),
> [#1340](https://github.com/google/leveldb/pull/1340),
> [#1344](https://github.com/google/leveldb/pull/1344) and
> [#1354](https://github.com/google/leveldb/pull/1354): google/leveldb
> `main` + the PR applied verbatim + the regression tests reproduces each
> defect (red: assert aborts and/or failed tests), and adding the
> corresponding fix alone turns them green with the rest of the suite
> passing. Two of this fork's guard-tests necessarily fail on a tree that
> lacks their guards (`OversizedBlockHandle`,
> `Harness.RejectsOverflowingEntryLengths` on the #1340 tree — their guards
> live in other commits and are excluded there). The #1354 tree additionally
> needs `-DHAVE_ZSTD=ON`: upstream's probe still looks for the lowercase
> `zstd_compress` symbol and silently disables zstd (see
> [a34d62a](https://github.com/takomitkm/leveldb/commit/a34d62a)), so the
> PR's own zstd tests skip without it.
>
> **How the Windows side was audited:** `env_posix.cc` and `env_windows.cc`
> were read side by side; every behavioural difference found is either in
> table A (fixed here), table B (documented, left open) or the "checked and
> found consistent" list. If a Windows/POSIX inconsistency is not listed
> there, this audit did not find it.
>
> **Where PRs were searched** — every PR, open and closed, in each repository
> below (PR scan finished 2026-11-04 00:17 UTC+8):
>
> | Repository | Result of the PR scan |
> |---|---|
> | google/leveldb | the six open PRs merged above; #755 (empty patch), #760 (conflicts), #850 (already fixed upstream) checked and excluded, see below |
> | pmmp/leveldb | one PR ever (#4, internal cleanup) — nothing usable |
> | Mojang/leveldb | **#8 and #16 adopted (tables C and A)**. Their default branch `master` mirrors upstream exactly; the real work sits on `main`, which is 11 commits ahead. The whole commit list was reviewed plus all 18 PRs: apart from #8 (CRLF in `CURRENT`) and #16 (CMake RTTI/exception scoping, which answers upstream issue #731), the rest is Minecraft build/app infrastructure and tuning (zlib support, a host-app allocator, JSON stats, level-0 limit tweaks, seek-compaction switches, log-buffer sizing, a `Get(nullptr)` API relaxation) — usable as patches here, but Mojang-specific features rather than fixes |
> | Amulet-Team/leveldb-mcpe | #6/#9/#10 are Minecraft-specific (CRLF/binary `CURRENT` handling, locking against Minecraft's own LOCK-ignoring clients) — only #6's defect is general, and it is covered by the Mojang #8 row; #11 "Crash Fixed" adds two blank lines and nothing else |
> | firebase/leveldb | podspec/release management; the only code PR (#4) sprinkles C-style casts to silence warnings — no behaviour change |
> | bitcoin-core/leveldb-subtree | surveyed at commit level (~40 commits on `bitcoin-fork`): the `file_size` initialization is the only useful non-upstream change; the rest is refactors, CI, C++ standard bumps and a breaking `GetName()` API addition |
> | oodrive/leveldb.net | PRs are README edits and a WIP; its `RenameFile` workaround lives in its vendored C++ copy, not in a PR |
> | danielgindi/leveldb | the #526 author's fork — no longer exists (404 as of this scan) |
>
> **Branch-level scan** — every branch of every repository above was then
> enumerated, dated by its latest commit, and compared against upstream `main`
> (`ahead_by > 0` = unique work, reviewed commit by commit; branch scan
> finished 2026-11-04 01:13 UTC+8). This is done per branch because a fork's
> default branch can silently mirror upstream while the real work sits on
> another branch — Mojang/leveldb is exactly that case (`master` = mirror,
> `main` = their actual work). Results per repository:
>
> - **google/leveldb** — its non-`main` branches (`reuse-manifest` from 2014,
>   `doc-testing` from 2019) are stale experiments whose content landed on
>   `main` long ago.
> - **pmmp/leveldb** — 7 branches: `master` and `no-handle-inheritance` mirror
>   upstream; `reuse-manifest` and `doc-testing` are byte-identical copies of
>   upstream's own stale branches; `env_rename` re-implements the
>   `Env::Remove{File,Dir}` API upstream already has; `mojang-compatible`
>   (24 commits) and `zlib-length-prefix` (17) are the Mojang compressor
>   framework (zlib/zstd/brotli/zopfli world formats) — Minecraft features,
>   not fixes.
> - **Mojang/leveldb** — the remaining 14 branches are the per-feature working
>   branches behind the PRs reviewed above (plus `leveldb_mirror`/`rc` mirrors
>   and the same 2011 Boost-era `windows` port firebase has) — nothing
>   additional.
> - **Amulet-Team/leveldb-mcpe** — 28 branches; this repo carries Mojang's
>   *original* leveldb-mcpe tree, and its 27 pre-2021 branches share no git
>   ancestry with upstream (judged against their own history instead). Two
>   general fixes were found: the `RepairTable` parse guard (**adopted**, table
>   C) and a 2017 variant of the CURRENT CRLF fix (third independent instance,
>   table C). The rest is obsolete or Minecraft-specific: fixes to the removed
>   `env_win`/`env_winrt` backends — including an event-handle-per-read leak
>   and a `FormatMessageW` bytes-vs-chars fix, neither of which applies to
>   upstream's Chrome port (no `CreateEvent`, `FormatMessageA` counts bytes
>   correctly) — Windows condvar/pread rework, compressor formats
>   (brotli/zstd/zopfli), Windows Phone/WinRT, build and CI work.
> - **firebase/leveldb** — 6 branches: `master`'s only unique commit is an iOS
>   bitcode build flag (2015), `android` is a 2011 NDK port, `windows` the
>   2011 Boost port, `firebase-release` is packaging; nothing general.
> - **bitcoin-core/leveldb-subtree** — one branch (`bitcoin-fork`), fully
>   mined at commit level; re-confirmed, nothing new.
> - **oodrive/leveldb.net** — not a GitHub fork (no common ancestry to
>   compare); branches are `master` and a Copilot-generated WIP updating the
>   vendored copy to 1.23 — no original C++ fixes.
>
> **Issues** (two passes, 2026-11-04): pass 1 reviewed the highest-volume
> issues (top 15 by comments, top 15 by reactions). Pass 2, after widening the
> net, cross-referenced **all 700 issues that have any discussion** against
> every PR ever filed (655) and deep-read the 14 unmerged PRs attached to the
> 29 candidate issues. Result: **three fixes adopted** — [PR #1350](https://github.com/google/leveldb/pull/1350)
> (2 GiB out-of-bounds read in `DecodeEntry`, issue #1348), [PR #1326](https://github.com/google/leveldb/pull/1326)
> (unbounded allocation via crafted block handles, issue #1318) and [PR #1359](https://github.com/google/leveldb/pull/1359)
> (size_t underflow on short internal keys, issue #103); the other eleven
> candidates were rejected — #1000 changes the public `Comparator` API,
> #762/#763/#764 (4 GiB values) each fix only half the path, #771 is an
> `Env` API redesign, #1031 adds a feature option, #815 is a one-line log
> typo, #1374 lacks a test, #869 only touches `db_bench`, and #385 (pthread
> detach) is obsolete — upstream's `StartThread` has been `std::thread` +
> `detach()` for years. The first pass's verdicts stand for the rest: #401
> was already fixed upstream (646c3588de84, in this tree's base), #783 and
> #182/#1082 are design debates without a patch, #425 ended as an unresolved
> caller-lifetime diagnosis, #1247 is a C++14 toolchain complaint. #195/#775
> remain bug-report context only.
>
> **CMake library detection under cross builds:** `check_library_exists()`
> compiles its probe but never links it, so under a cross toolchain whose
> sysroot lacks crc32c/snappy/zstd every `HAVE_*` turns into a false positive
> and the build breaks. Known upstream discussion: [#1146](https://github.com/google/leveldb/issues/1146)
> (the false-negative twin), [PR #809](https://github.com/google/leveldb/pull/809)
> (FindSnappy module, open since 2020), and [#684](https://github.com/google/leveldb/issues/684)
> (iOS cross build, closed as an iOS issue). This fork fixes it minimally:
> the `HAVE_*` checks are cache variables a cross build pins explicitly
> (`-DHAVE_CRC32C=OFF ...`), which is compatible with a future Find-module
> approach.
>
> Other
> LevelDB implementations — RocksDB, goleveldb, Chromium's
> `third_party/leveldatabase` — appear above only as prior-art evidence
> in the downstream-workaround column, never as patch sources.
>
> **Who made this:** the entire tree was assembled by AI coding agents and has
> **not been human-reviewed**. Every provenance claim above is a link that can
> be checked. Use at your own risk.
>
> **To pick up new work later:** everything up to the 2026-11-04 branch scan
> (01:13 UTC+8) has been through this process once, and the unmerged-PR sweep
> was re-run on 2026-11-07 (eight more backports, table C, each then audited
> per commit). A future pass only needs
> to look at google/leveldb PRs with `is:pr created:>=2026-11-08`, re-run the
> same PR + branch scan on the fork list above, and re-diff
> `bitcoin-core/leveldb-subtree` and `Mojang/leveldb` `main` against upstream.
>
> Use at your own risk; patches are offered as-is, in the hope they are useful
> upstream.
