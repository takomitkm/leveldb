**向后兼容性尚未做好，暂不建议直接使用或替换上游 LevelDB。**

**Backward compatibility is incomplete and has not been fully validated.**
Do not treat this fork or its binary packages as a ready-to-use replacement
for upstream LevelDB, or use them with important databases before validating
your application and deployment. Individual fixes and passing tests do not
establish complete compatibility.

See [section E](#e-backward-compatibility-audit--向后兼容性审查-2026-11-07)
and the [step-by-step compatibility checklist (中文)](docs/BACKWARD_COMPATIBILITY_CHECKLIST.zh-CN.md)
for confirmed historical defects, current fixes, remaining decisions and
checks that still need to be performed.


**Purpose:** collect and merge the Windows-related pull requests that have been
sitting unreviewed upstream for years, plus data-loss fixes that had no
proposal at all, so that Windows users get a single tree where they all apply
together. **This is not an official leveldb release.** Upstream `master` is
untouched; everything here is meant to be offered back upstream.

**Windows download choice:** the new release packaging distinguishes `-acp`
packages for original Win32 narrow-code-page paths from `-utf8` packages for
explicitly UTF-8 paths. Older assets do not all follow this convention.
Both path variants retain the safety fixes. See the [compatibility guide](docs/COMPATIBILITY.md)
for build options, CRT/compiler requirements, codec support and behavioural
differences; selecting a path variant alone does not establish compatibility.

**Contents (all sources listed).** Tables A–C cover Windows↔POSIX fixes,
remaining platform inconsistencies, and other fixes. Their last two columns
record downstream workarounds and CI guards. Table D lists the tests/CI added
by this fork. **Section E (at the end of this file) records the backward
compatibility audit** and links the detailed execution checklist; earlier
platform-parity conclusions are not a guarantee that downstream applications
can directly replace their library.

**How to tell what is original here.** When the *Change* title is a link — e.g.
[Durable `RenameFile`](https://github.com/takomitkm/leveldb/commit/3951ee8) — the row is this fork's own work and the link points at the
introducing commit. Titles in plain bold came from someone else's PR or
issue, cited in the *Source* cell. Table D collects the fork's own tests and
CI work, each row linked the same way.

**What I did upstream** records which rows have already been acted on outside this
fork, so the rest can be triaged by eye: an empty cell means nothing has been sent
yet (still open); `PR #NNNN` or `comment on #NNNN` means a PR was opened or a
comment was posted there and the row needs no further action; `—` marks fork-only
work with no upstream counterpart; `deliberate` marks a decision not to offer the
change, with the reason in the cell.

### A. Windows ↔ POSIX inconsistencies — fixed in this fork

These are cases where `env_posix.cc` and `env_windows.cc` behave differently
and the Windows side is the weaker one.

| Change | POSIX side | Windows side (before) | Source | Downstream workaround (no upstream fix) | CI guard | What I did upstream |
|---|---|---|---|---|---|---|
| **[Durable `RenameFile`](https://github.com/takomitkm/leveldb/commit/3951ee8)** | `Sync()` calls `SyncDirIfManifest()` so the manifest's directory entry is flushed | rename returned before being durable, while `DeleteObsoleteFiles()` pruned files from *in-memory* state → `CURRENT` could be left pointing at a deleted manifest | **[3951ee8](https://github.com/takomitkm/leveldb/commit/3951ee8)** — no prior proposal (written for this fork); relates to [#195](https://github.com/google/leveldb/issues/195), [#775](https://github.com/google/leveldb/issues/775) | **Chromium**: `leveldb_chrome.cc` `DeleteDB()` = `DestroyDB` + `base::DeletePathRecursively`, and its callers log "corrupted (and deleted) database" — i.e. delete and rebuild. **Syncthing / IPFS (kubo) / Prometheus / aptly**: documented "delete the DB and let it rebuild" after CURRENT/MANIFEST corruption. **oodrive/leveldb.net**: replaced `RenameFile` with `MoveFileW` + delete-then-retry (cures the #775 symptom, not durability). **Minecraft (leveldb-mcpe)**: `DeleteFileW` + `MoveFileExW(flags=0)` — no durability either. **Rime (librime)**: repair the db at `Open()` time ([rime/librime#1260](https://github.com/rime/librime/pull/1260), in review) — recovery, not prevention. | `win-quick`; `build-and-test` windows/msvc (`TestRenameFileDurableSemantics`, `TestRenameFileReplacesExisting`) | PR [#1383](https://github.com/google/leveldb/pull/1383); comment on [#775](https://github.com/google/leveldb/issues/775) |
| **Short read handling** in `SequentialFile::Read` | loops until `n` bytes or EOF | a short `ReadFile` was treated as EOF, silently truncating WAL/MANIFEST reads | [PR #1334](https://github.com/google/leveldb/pull/1334) (open since 2026, data loss) | none found | `leveldb_tests` (all `build-and-test` cells) | comment on [#1334](https://github.com/google/leveldb/pull/1334) |
| **Precise `NowMicros()`** | `clock_gettime` (µs) | coarse system timer | [PR #1366](https://github.com/google/leveldb/pull/1366) (open since 2026) | none found | `leveldb_tests` |  |
| **[Appending `NewLogger`](https://github.com/takomitkm/leveldb/commit/7c242f8)** | `NewLogger()` opens with `O_APPEND \| O_CREAT` — the LOG file grows across opens | `NewLogger()` opened with `fopen(..., "wN")`, truncating the diagnostic LOG at every open and destroying the previous run's diagnostics | **[7c242f8](https://github.com/takomitkm/leveldb/commit/7c242f8)** — written for this fork (one-line `fopen` mode change to `"aN"`, keeping the non-inheritable `"N"` flag) | none needed (cosmetic; upstream still truncates) | `win-quick` (`LoggerAppendsAcrossOpens`) | comment on [#850](https://github.com/google/leveldb/pull/850) — the `wN` truncation; fix is `aN` |
| **[Parent-directory sync for manifests](https://github.com/takomitkm/leveldb/commit/30b34e0)** | `Sync()` → `SyncDirIfManifest()` opens the parent directory and fsyncs it **before** the manifest data lands | `Sync()` said "On Windows no need to sync parent directory" and only called `FlushFileBuffers` on the file — a power failure could lose a newly created manifest's directory entry even though its data was written, leaving `CURRENT` pointing at a file that no longer exists | **[30b34e0](https://github.com/takomitkm/leveldb/commit/30b34e0)** — written for this fork: `SyncDirIfManifest()` mirrors the POSIX order, opening the parent directory with `CreateFileW(GENERIC_WRITE, FILE_FLAG_BACKUP_SEMANTICS)` and flushing it with `FlushFileBuffers()` (verified empirically on NTFS as a non-admin user on two volumes; the handle needs `GENERIC_WRITE` — a read-only directory handle fails with `ERROR_ACCESS_DENIED` — and no administrator privilege is involved). No prior proposal exists upstream. The documentary basis (MS-FSA §2.1.5.7 product note `<80>` plus two Microsoft Q&A threads), the E13 measurement that could not produce valid data (superseded by the E14/E15 block-command tracing — same section), the measured cost of the extra flush, and a survey of who else flushes a directory on Windows are recorded in [section F](#f-windows-directory-flush-who-does-it-who-skips-and-what-it-costs-2026-11-14). | **RocksDB** (`WinDirectory::Fsync` is a no-op) and **Chromium** skip it as well; the survey of non-DB projects that do flush is in section F. | `win-quick` (`ManifestSyncFlushesDirectory`) | PR [#1388](https://github.com/google/leveldb/pull/1388) (offered 2026-11-14, branch `win-manifest-dirsync`; the E14/E15 traces superseded the inconclusive E13 that had kept this deliberate-not-offered; history and survey in the PR's follow-up comment) |
| [Regression test `TestRenameFileReplacesExisting`](https://github.com/takomitkm/leveldb/commit/3951ee8) | — | covers the `ReplaceFile` / `ERROR_UNABLE_TO_REMOVE_REPLACED` case from [#775](https://github.com/google/leveldb/issues/775) | [3951ee8](https://github.com/takomitkm/leveldb/commit/3951ee8) | n/a | `win-quick` (`TestRenameFileReplacesExisting`) | part of PR [#1383](https://github.com/google/leveldb/pull/1383) |
| **[Chunked non-mmap reads for requests of 4 GiB and more](https://github.com/takomitkm/leveldb/commit/0a472d3)** | `pread()` takes a `size_t` and serves any length | the request length went straight into `ReadFile`'s `DWORD` parameter: a read of exactly 2^32 truncated to **zero** and reported an empty result for data that was actually there — a reachable short-read-after-corruption for legitimate large blocks, since `ReadBlock` sizes its request from the block handle. mmap-backed reads were unaffected | **[0a472d3](https://github.com/takomitkm/leveldb/commit/0a472d3)** — written for this fork after the post-merge review; requests are chunked at the `DWORD` limit with the previous short-read semantics (one `ReadFile` call still for everything below 4 GiB) | none found | `win-quick` (`env_windows_test` `HugeReadRequestServedInChunks`) |  |

### B. Windows ↔ POSIX inconsistencies — still open upstream (documented, not changed here)

**None remaining.** Every inconsistency the audit found is either fixed in
table A or on the "checked and found consistent" list. The parent-directory
sync — the last entry this table ever held, and the Windows counterpart of
[#195](https://github.com/google/leveldb/issues/195) — was fixed in this
fork on 2026-11-04 (see table A). Upstream still has both gaps.

**Checked and found consistent** (so nobody has to audit them again): file
mapping (both use mmap — `CreateFileMappingW`/`MapViewOfFile` on Windows),
`SleepForMicroseconds` (both `std::this_thread::sleep_for`), `GetChildren`
(both non-recursive), `LockFile` (both exclusive — `LockFile` region locks vs `fcntl`),
and handle inheritance (`NewLogger` already uses `"wN"`, and `CreateFileW`
handles are not inheritable by default).

### C. Other fixes (not platform parity)

| Change | Source | Upstream status | Downstream workaround (no upstream fix) | CI guard | What I did upstream |
|---|---|---|---|---|---|
| UTF-8 → UTF-16 path conversion for all file APIs in `env_windows.cc` (Windows-only capability; POSIX paths are byte strings) | [PR #988](https://github.com/google/leveldb/pull/988) by the original author | open since 2022; **fixed here to compile with GCC** (passed `false` where `LPBOOL` was expected). **Note the contract change:** upstream's `...A` APIs interpret path bytes as the system ANSI code page, while this PR interprets them as UTF-8. Callers that hand over ACP-encoded paths — for example MSVC's `std::filesystem::path::string()`, which converts to the ANSI code page — must switch to `u8string()`, otherwise non-ASCII paths stop working. Callers can instead select the `-acp` package or set `LEVELDB_WINDOWS_UTF8_PATHS=OFF` to retain native-code-page paths without changing their calls. The UTF-8 package keeps the UTF-8 contract. Both variants retain the safety fixes. | **bitcoin-core/leveldb-subtree**: full ANSI→Wide conversion in its `bitcoin-fork`. **Minecraft (leveldb-mcpe)** and **oodrive/leveldb.net**: their own `env_win.cc` already use the `W` APIs. | `build-and-test` all cells (compiles); `win-quick` (`TestOpenOnRead`) | comment on [#988](https://github.com/google/leveldb/pull/988) |
| Compiler warning flags for Windows builds | [PR #1214](https://github.com/google/leveldb/pull/1214) | open since 2024; **fixed here to apply to Clang only** (`-Wno-deprecated-declarations` makes MSVC `cl` fail with D8021) | none (build-only) | `build-and-test` windows/clang | comment on [#1214](https://github.com/google/leveldb/pull/1214) |
| **Do not reuse a log that ends with an incomplete record** (platform-independent) | [PR #1336](https://github.com/google/leveldb/pull/1336) | open since 2026; data loss | none found; Chromium instead enables upstream's manifest-reuse feature | `leveldb_tests` |  |
| Handle short reads in `PosixRandomAccessFile::Read` (the POSIX-side twin of #1334) | [PR #1368](https://github.com/google/leveldb/pull/1368) | open since 2026 | none found | `leveldb_tests` |  |
| Initialize `file_size` in `CompactionState::Output` (uninitialized read / UB, platform-independent) | [bitcoin-core/leveldb-subtree@ad9b1c9](https://github.com/bitcoin-core/leveldb-subtree/commit/ad9b1c989380538bfb19c5c65df0d3f72d8ed62b) | not upstream; cherry-picked from Bitcoin Core's maintained fork | fixed **only** in bitcoin-core's fork (that is the source above) | `leveldb_tests` (Boltdb/bitcoin fork patch, no test upstream) |  |
| **Reject overflowing block entry lengths in `DecodeEntry`** — the uint32 bounds check wrapped for crafted entries near 2 GiB and the iterator read out of bounds (platform-independent) | [PR #1350](https://github.com/google/leveldb/pull/1350) → issue [#1348](https://github.com/google/leveldb/issues/1348), open since 2026, found via the issue scan | upstream as 29465588d3 (official Piper rewrite of the same fix, landed 2026-11-04; PR #1350 itself still open); includes the `RejectsOverflowingEntryLengths` regression test — kept alongside upstream's `CorruptedBlockEntryLengthOverflow` after the 2026-11-17 merge | none found | `leveldb_tests` (`RejectsOverflowingEntryLengths`) | comment on [#1348](https://github.com/google/leveldb/issues/1348) (now fixed upstream as 29465588d3) |
| **Validate block handles against the file size before `ReadBlock`** — a crafted footer/index requested an unbounded read (allocation DoS); index, metaindex, filter and data handles are now bounds-checked (platform-independent) | [PR #1326](https://github.com/google/leveldb/pull/1326) → issue [#1318](https://github.com/google/leveldb/issues/1318), open since 2026, found via the issue scan | not upstream; includes the `OversizedBlockHandle` proof-of-concept regression test | none found | `leveldb_tests` (`OversizedBlockHandle`) |  |
| **Guard against internal keys shorter than 8 bytes** — `ExtractUserKey` underflowed to a near-`SIZE_MAX` slice on truncated keys in release builds; short keys are now rejected at `DecodeFrom` (platform-independent) | [PR #1359](https://github.com/google/leveldb/pull/1359) → issue [#103](https://github.com/google/leveldb/issues/103) (created 2014-09, closed 2016-01 as stale; a [2020 comment](https://github.com/google/leveldb/issues/103#issuecomment-577044569) reports crashes again, and the release-build underflow behind it is still on master), found via the issue scan | not upstream | none found | `leveldb_tests` |  |
| **Tolerate CRLF line endings in `CURRENT`** — a `\n` or `\r\n` (or lone `\r`) terminator is accepted and the `\r` is stripped before the manifest name is used; a `CURRENT` with no terminator is still rejected as a torn write | [mojang/leveldb#8](https://github.com/Mojang/leveldb/pull/8), merged into Minecraft Bedrock (2024-12) | not proposed upstream; the same defect was fixed independently **three** times downstream: Mojang's original leveldb-mcpe tree ([commit 52edeaab](https://github.com/Amulet-Team/leveldb-mcpe/commit/52edeaab), 2017), [Amulet-Team/leveldb-mcpe#6](https://github.com/Amulet-Team/leveldb-mcpe/pull/6) (2021), and mojang/leveldb#8 (2024). A Windows text tool that rewrites `CURRENT` with CRLF leaves a `\r` inside the manifest file name, and every later `DB::Open()` fails with `Corruption: CURRENT points to a non-existent file` | **Minecraft** and **Amulet (leveldb-mcpe)**: this exact fix, carried in their own forks; upstream has nothing | `leveldb_tests` (`CurrentFileTerminator.*`, added in [3806b66](https://github.com/takomitkm/leveldb/commit/3806b66)) |  |
| **`RepairTable()` skips entries whose internal key cannot be parsed** — the metadata pass in `ScanTable()` already skipped unparsable keys, but the table-rebuilding copy loop did not, so unparseable entries could reach a rebuilt table | [Amulet-Team/leveldb-mcpe@52253f2f](https://github.com/Amulet-Team/leveldb-mcpe/commit/52253f2f) (from Mojang's original leveldb-mcpe tree, 2018, fixed for a real Bedrock production bug) | not proposed upstream | **Minecraft ecosystem**: this fix, carried in their own trees | `leveldb_tests`; `fuzz` (recovery harness) |  |
| **[Scope the MSVC/GCC exception/RTTI settings to the leveldb target, and make both optional](https://github.com/takomitkm/leveldb/commit/aa0c122)** — upstream rewrites the global `CMAKE_CXX_FLAGS` with `string(REGEX REPLACE ...)` and hard-codes both switches, so there is no way to build leveldb with RTTI: a consumer that uses `typeid`/`dynamic_cast` on leveldb types cannot link (`undefined reference to typeinfo for leveldb::Comparator` — [#731](https://github.com/google/leveldb/issues/731); Ceph hits the same for `leveldb::Logger`). An earlier version of this row claimed the global rewrite also stripped RTTI/exceptions from the consumer's **own** targets; that was wrong, and the correction is worth recording: `CMAKE_CXX_FLAGS` set inside an `add_subdirectory()` scope does not reach sibling targets in the parent directory. Verified 2026-11-14 with a consumer project (`add_subdirectory(leveldb)` plus a sibling target and an app target): in the upstream build the compile commands for the sibling and the app carry neither `-fno-rtti` nor `-fno-exceptions` (read from `compile_commands.json`), so the flags never left leveldb's directory scope. The real symptom is the missing opt-in, not flag leakage. **This fork deviates from Mojang#16's implementation**: that PR applies the settings with directory-wide `add_compile_options()` / `add_definitions()`, which its own review thread questioned (rohunMS asked for `target_compile_options()`; the author answered that benchmarks/tests being affected is acceptable and merged) — and it does break the build: with the flags no longer inside `CMAKE_CXX_FLAGS`, the Kyoto Cabinet probe now *succeeds* where upstream's silently failed, so `db_bench_tree_db` actually gets compiled and its system headers (`kcutil.h`) require exceptions, failing the ubuntu/gcc jobs. Here the flags are `target_compile_options(leveldb PRIVATE ...)` (the reviewer's shape), and the probe itself is fixed: it re-enables exceptions for its own `try_compile` (`CMAKE_REQUIRED_FLAGS`, restored afterwards) so the result answers "is Kyoto Cabinet available?" rather than "does it compile under whatever flags the consumer forces?", and `db_bench_tree_db` gets `-fexceptions` explicitly because that TU cannot compile without it. **Exceptions are on by default** so the library matches how consumers build against the public headers (`_HAS_EXCEPTIONS=1`); `-DLEVELDB_DISABLE_EXCEPTIONS=ON` opts out (and sets `_HAS_EXCEPTIONS=0` on MSVC — as a **directory-level** definition, because the STL headers are inlined into every TU here: scoping it to the library alone would leave the library's STL at 0 and the tests'/benchmarks' at 1, giving the same inline functions two definitions (ODR violation); a directory property does not reach `add_subdirectory()` consumers, so they stay untouched). **RTTI also follows the compiler default (on)** for the same reason: with `-fno-rtti` on the library alone its targets emit no `typeinfo` for polymorphic classes such as `leveldb::WritableFile`, while consumer targets compiled at the compiler default still reference it and the link fails (`undefined reference to typeinfo for leveldb::WritableFile`, reproduced with GCC 15.2 on Ubuntu); `-DLEVELDB_DISABLE_RTTI=ON` opts in to upstream's RTTI-off codegen, with the documented constraint that all consumers must disable RTTI as well. Verified on MSVC and MinGW in both profiles | [Mojang/leveldb#16](https://github.com/Mojang/leveldb/pull/16) by Yeabsira Mekonnen (Microsoft), merged into Minecraft Bedrock (2025-02-28); supersedes the author's earlier [Mojang/leveldb#14](https://github.com/Mojang/leveldb/pull/14) | offered upstream 2026-11-14 as **[PR #1387](https://github.com/google/leveldb/pull/1387)** — the minimal shape: two `option()`s guarding the existing `string(REGEX REPLACE ...)` code, both ON by default, so nothing about the default build or ABI changes; verified in a consumer project (upstream default fails to link, patched with `-DLEVELDB_DISABLE_RTTI=OFF` links and runs, patched default fails exactly like upstream, the sibling target is unaffected in every build). It fixes the exact complaint of [#731](https://github.com/google/leveldb/issues/731) (open since 2019; Mojang's fork is the only earlier implementation, and it covers RTTI only). This fork's implementation below goes further: target-scoped flags, an exceptions switch, and the probe/benchmark fixes. Turning RTTI off in 1.23 was also an ABI break with no soname bump: it broke Ceph (undefined `typeinfo` references), plyvel, and forced Fedora to carry a patch — see [#731](https://github.com/google/leveldb/issues/731) and [#927](https://github.com/google/leveldb/issues/927). Note: upstream's gcc jobs never exercised `db_bench_tree_db` — the global `-fno-exceptions` in `CMAKE_CXX_FLAGS` made the detection probe fail, so the target was never generated: they **skip** that benchmark rather than pass it. This fork's probe fix is verified on gcc by an A/B configure test: pre-Mojang#16 `CMakeLists.txt` reports `Performing Test HAVE_KYOTOCABINET - Failed` and no target, the fixed one reports `- Success` and generates `db_bench_tree_db` with `-fexceptions` appended after the consumer's `-fno-exceptions` | **Mojang/Minecraft** (source of this fix), **Ceph**, **plyvel**, and every distro packaging leveldb: each carries its own patch or build flag | `no-exceptions` job; `build-and-test` all cells (RTTI default pinned in [18b79bb](https://github.com/takomitkm/leveldb/commit/18b79bb)) | PR [#1387](https://github.com/google/leveldb/pull/1387) (minimal upstream-shaped variant) |
| **[Restore the original Windows path-code-page contract as a build option](https://github.com/takomitkm/leveldb/commit/6b5edb6)** — `LEVELDB_WINDOWS_UTF8_PATHS=OFF` selects the original narrow file API encoding; ON retains UTF-8. Logger and temporary-directory paths follow the same selection | written for this fork | not proposed upstream; preserves caller path bytes without removing the safety fixes | choose the `-acp` binary or the source-build option | `compatibility` (both path modes, calibrated mutations); `release` (extracted-package consumer) |  |
| **[Honor custom sequential-file buffers during log recovery](https://github.com/takomitkm/leveldb/commit/6b5edb6)** — preserve each returned slice before another short read can overwrite a reusable external buffer | written for this fork; fixes a contract violation in the PR #1334 patch itself (short-read chunks are counted but never copied into the caller's scratch, so an Env returning its own buffer loses data) | reported upstream 2026-11-08 ([comment](https://github.com/google/leveldb/pull/1334#issuecomment-6714682990)); public `SequentialFile::Read` explicitly permits a result outside scratch | no caller workaround needed | `compatibility` (`ReadFromReusedBuffer`, `ShortReadsFromReusedBuffer`, calibrated mutation); full test suites | comment on [#1334](https://github.com/google/leveldb/pull/1334) — this fix grew out of that review |
| **Reject a `UINT64_MAX` `next_file_number` recovered from a MANIFEST** — the sentinel value is a corruption marker; recovery computed `next + 1`, which wrapped the counter to zero, so subsequent files were numbered 0, 1, 2, … and collided with the numbers of live tables and logs (platform-independent) | [PR #1377](https://github.com/google/leveldb/pull/1377), open since 2026 | not upstream | none found | `leveldb_tests` (`RecoveryTest.ManifestNextFileNumberOverflow`) |  |
| **Bound the decompression path before allocating** — the declared decompressed size is sanity-checked (sentinels, `SIZE_MAX` representability), large declarations are probed with a bounded streaming pass before the output allocation, and that allocation is `nothrow` (platform-independent) | [PR #1354](https://github.com/google/leveldb/pull/1354), open since 2026; the upstream hardening commits were cherry-picked into this fork as [652e9a7](https://github.com/takomitkm/leveldb/commit/652e9a7cbe61515321c28d3d615a8f2678eeef20) and [8f4d6f7](https://github.com/takomitkm/leveldb/commit/8f4d6f7e56c8044e226f5fab638bbef04e361f99) with their original git authorship kept (the hardening commit’s author is [fredlday-oss](https://github.com/fredlday-oss) <243643093+fredlday-oss@users.noreply.github.com>, carried over from the upstream re-authoring [186a123](https://github.com/google/leveldb/commit/186a1237210efdd7a801fa6614e8478efb2e370e); the initial push had used a placeholder address) | not upstream; includes the `ReadBlockIssue1349Test` suite | none found | `leveldb_tests`; `optional-deps` (the only job where the zstd cases execute — the regular cells have no zstd and skip them — and the job fails if any of them skips or the suite shrinks below 12) | comments on [#1354](https://github.com/google/leveldb/pull/1354) |
| **Reject blocks whose restart offset exceeds 32 bits** — the decoded block's restart array must end inside the `uint32_t` range the format stores, otherwise the offset truncated and iterators read out of bounds (platform-independent) | [PR #1367](https://github.com/google/leveldb/pull/1367), open since 2026; fixes the >4 GiB data loss family | not upstream | none found | `leveldb_tests` |  |
| **Reject block handles that cannot represent their raw read** — sizes that overflow `size_t` or wrap with the 5-byte trailer are rejected before any arithmetic result is used (platform-independent) | [PR #1380](https://github.com/google/leveldb/pull/1380), open since 2026 | not upstream | none found | `leveldb_tests` (`OversizedBlockHandle`, `ReadBlockIssue1349Test`) |  |
| **Reject varints whose last byte overflows the decoded value** — `80 80 80 80 10` used to decode as 0 instead of failing (platform-independent) | [PR #1382](https://github.com/google/leveldb/pull/1382), open since 2026 | not upstream; legal redundant encodings (`80 00`, …) are still accepted | none found | `leveldb_tests` (`Coding.Varint32OverflowInLastByte`, `Coding.Varint64OverflowInLastByte`) |  |
| **Drop tables with zero parseable keys during repair** — such a table is archived into `lost/` instead of being published with empty metadata (platform-independent) | [PR #1344](https://github.com/google/leveldb/pull/1344), open since 2026 | not upstream | none found | `leveldb_tests`; `fuzz` (recovery harness) | comment on [#1344](https://github.com/google/leveldb/pull/1344) |
| **Flush the pending data block before applying `ChangeOptions`** — the open block was previously finished under the new settings, tripping the block-builder assert (platform-independent) | [PR #1340](https://github.com/google/leveldb/pull/1340), open since 2026 | not upstream | none found | `leveldb_tests` (`TableTest.ChangeOptions*`, added by the follow-up below) | comment on [#1340](https://github.com/google/leveldb/pull/1340) |
| **Reject invalid internal keys when decoding MANIFEST edits** — `InternalKey::DecodeFrom` now validates the whole key (length *and* value type), the comparator orders undersized keys by length instead of decoding an out-of-bounds trailer, and `BuildTable`/compaction reject iterators producing malformed keys (platform-independent) | [PR #1335](https://github.com/google/leveldb/pull/1335), open since 2026 | not upstream; the error paths of the rejection were fixed in the follow-up below ([bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e)) | none found | `leveldb_tests` (`VersionEditTest.RejectShortInternalKeyInNewFile`, `dbformat_test`, `BuilderTest`) | comment on [#1335](https://github.com/google/leveldb/pull/1335) |
| **[Fail compaction and `BuildTable` on invalid internal keys instead of dropping them](https://github.com/takomitkm/leveldb/commit/bf7212e)** — the tightened validation above shipped with two broken error paths: compaction skipped unparsable records *without* recording an error and could still succeed (silently destroying the input records and the evidence of what was wrong), and `BuildTable`'s rejection paths deleted the builder without `Abandon()`, aborting on the destructor assert instead of returning the corruption status. Compaction now fails with `Corruption` (input files stay referenced), `BuildTable` validates the first key before creating anything and routes interior rejections through one cleanup, and `GetInternalKey` delegates to `DecodeFrom` so the rules live in one place | written for this fork; follow-up to the #1335 backport ([72be98c](https://github.com/takomitkm/leveldb/commit/72be98c)), found by the 2026-11-07 per-commit review | not upstream | none found | `leveldb_tests` (`CorruptionTest.CompactionFailsOnInvalidInternalKey`, `BuilderTest.*`) | comment on [#1335](https://github.com/google/leveldb/pull/1335) — covered the two broken error paths |
| **[Reject invalid `ChangeOptions` calls and surface flush failures](https://github.com/takomitkm/leveldb/commit/9789dab)** — the newly added flush (above) gave `ChangeOptions` two unhandled error paths: after `Finish()`/`Abandon()` it still touched the (possibly destroyed) file, and an I/O error it raised itself was stored in the sticky status while the call returned OK with the options swapped. `ChangeOptions` now rejects closed builders and filter-policy/`block_restart_interval` changes, returns its own flush errors, and leaves the options untouched on any failure — which also fixes the pre-existing dynamic `filter_policy` swap (a `Finish()` null dereference) and non-positive restart intervals | written for this fork; follow-up to the #1340 backport ([71c67e7](https://github.com/takomitkm/leveldb/commit/71c67e7)); the policy/interval gaps are baseline defects. **Collision (2026-11-17):** the same filter-policy check was proposed upstream as [#1381](https://github.com/google/leveldb/pull/1381) (Huang-404-Q, 2026-11-03, four days before 9789dab) — identical check and message string; this commit does not cite #1381 (the overlap surfaced in the later provenance audit), and an earlier independent instance exists in [puji4810/Prism](https://github.com/puji4810/Prism)'s from-scratch TableBuilder (guard present since 2025-12-25). #1381 is the primary upstream proposal for the filter-policy piece | not upstream; the filter-policy half is pending upstream review as [#1381](https://github.com/google/leveldb/pull/1381) (open) | none found | `leveldb_tests` (`TableTest.ChangeOptions*`) | comment on [#1340](https://github.com/google/leveldb/pull/1340) — covered both error paths |
| **[Archive empty tables under the file name that is actually there](https://github.com/takomitkm/leveldb/commit/0959c2f)** — `ScanTable()` locates a table through a `.ldb`-first/`.sst`-fallback probe, but the empty-table branch above re-generated the `.ldb` name for archiving; for an old `.sst` table the rename silently failed, the original stayed behind, was excluded from the rebuilt MANIFEST, and the next `Open()` deleted it — losing the offline evidence | written for this fork; follow-up to the #1344 backport ([01b0196](https://github.com/takomitkm/leveldb/commit/01b0196)), reproduced end-to-end | not upstream | none found | `leveldb_tests` (`RepairArchivesEmptyTableUnderActualName`) | comment on [#1344](https://github.com/google/leveldb/pull/1344) |
| **[Recompute rebuilt-table metadata from the records actually kept](https://github.com/takomitkm/leveldb/commit/4b6a977)** — `ScanTable()` that fails with a transient I/O error hands the file to `RepairTable()` with empty boundaries and sequence zero; publishing those wrote an empty `smallest` key (encode assert in debug, corrupt MANIFEST boundary in release) and recovered `LastSequence=0`, so the next write re-used sequence numbers and lost to the repaired record | written for this fork; baseline defect, found by the 2026-11-07 review | not upstream | none found | `leveldb_tests` (`RepairTableRecomputesMetadataAfterTransientError`) | PR [#1386](https://github.com/google/leveldb/pull/1386) (the #1344 comment mentioned it only in passing) |
| **[Make the Zstd pre-validation probe accept what one-pass decoding accepts](https://github.com/takomitkm/leveldb/commit/edba255)** — the streaming probe inherited zstd's default 2^27 window limit (checked against the *declared* window), so a frame declaring a 2^28 window with a 9 MiB payload — accepted by one-pass decoding, which officially ignores the window — was reported corrupt before producing a byte, and a frame ending with input left over rejected legal frame sequences such as a trailing zero-payload skippable frame. The probe now raises `ZSTD_d_windowLogMax` to 30 (covering every window a real encoder declares), continues with the next frame in the same context, and requires the accumulated output to equal the declared size. Measured with zstd 1.5.7 | written for this fork; follow-up to the #1354 backport ([719f24b](https://github.com/takomitkm/leveldb/commit/719f24b)), with the window policy chosen from a measured accept matrix | not upstream | none found | `leveldb_tests`; `optional-deps` (the only job where the zstd `ReadBlockIssue1349Test` cases execute, guarded to run rather than skip) | comments on [#1354](https://github.com/google/leveldb/pull/1354) |
| **Report the raw block buffer allocation failure instead of throwing** — the initial read buffer used throwing `new`, so `std::bad_alloc` could escape through a `Status`-returning interface, inconsistent with the decompression buffers in the same function | written for this fork; follow-up to the #1354 backport | not upstream | none found | `leveldb_tests` (`ReportsAllocationFailureForHugeRawRead`) | comment on [#1354](https://github.com/google/leveldb/pull/1354) — the nothrow fix was offered there |

**How the two path encodings coexist today.** With upstream's `...A` APIs the
round trip is consistent as long as callers also hand over ANSI-code-page
bytes: MSVC's `std::filesystem::path::string()` converts the internal UTF-16 to
the system code page (e.g. GBK on a Chinese system), and `CreateFileA` reads
those bytes back as the same code page. That is why an application can work on
a Chinese Windows install without #988 — until a path contains a character the
ANSI code page cannot represent (emoji, some rare ideographs), where
`path::string()` throws or drops data. Content is unaffected either way: keys
and values, including CJK text, are opaque bytes to leveldb and never go
through a code-page conversion.

**Why the durability fix matters:** on Windows, `RenameFile` returned before the
rename was durable, and `DeleteObsoleteFiles()` decides what to keep from
*in-memory* state rather than from the on-disk `CURRENT`. A crash — or a
transient rename failure such as antivirus holding the file open — can
therefore leave `CURRENT` pointing at a manifest that was already deleted, and
every later `DB::Open()` fails with `Corruption: CURRENT points to a
non-existent file`. `MOVEFILE_WRITE_THROUGH` makes the rename durable before
returning, closing that window. The Windows port also skips parent-directory
syncing by design (`env_windows.cc`, "On Windows no need to sync parent
directory"), which is the Windows counterpart of #195.

**Verified:** builds and passes its own test suite (`leveldb_tests`, `c_test`,
`env_windows_test`) with **MSVC 19.44** and **MinGW-w64 GCC 16.2** on Windows 11
x64 — 3/3 tests each. The rename test, the CRLF test (`CurrentFileWithCrlf`),
the logger test (`LoggerAppendsAcrossOpens`) and the manifest-sync test
(`ManifestSyncFlushesDirectory`) pass on both toolchains.
The opt-in fuzz harnesses in [`fuzz/`](fuzz/README.md) (public API and
recovery/corruption paths) also pass on both toolchains, and the whole
suite passes under AddressSanitizer (MSVC, `/MT` — the `/MD` runtime
miscompiles `std::mutex` under ASan). Verified 2026-11-04 (UTC+8).

**Verified 2026-11-07 (UTC+8):** the 2026-11-07 wave (eight backports, six
follow-up fixes, boundary tests) passes the full suite — `leveldb_tests`
with 263 cases, `c_test`, `env_windows_test` — on MinGW-w64 GCC 16.2 Debug
with Snappy and Zstd enabled, in a fresh out-of-tree build. Each fix in the
wave was additionally verified **red** against its pre-fix parent commit
before landing (the regression tests of table C fail on the parent and pass
here), and the fork's CI matrix is green on
[07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8).

**RTTI default changed 2026-11-05:** `LEVELDB_DISABLE_RTTI` now defaults to
**OFF**. The default build compiles the library with the compiler's own RTTI
setting (enabled), matching how consumer targets are compiled — including
this repo's own `leveldbutil`, `db_bench*` and test executables, which keep
the compiler default because the no-RTTI flag is target-scoped
(`target_compile_options(leveldb PRIVATE ...)`) and cannot propagate to
them. With the previous default (ON), the library was built `-fno-rtti` and
emitted no `typeinfo` for polymorphic classes (`leveldb::WritableFile`,
`leveldb::Comparator`, ...), so consumer targets failed to link:
`undefined reference to typeinfo for leveldb::WritableFile` (reproduced with
GCC 15.2 on Ubuntu). Making the flag opt-in follows the
`ROCKSDB_DISABLE_RTTI` convention and answers the demand of upstream
[#731](https://github.com/google/leveldb/issues/731), offered there as PR
[#1387](https://github.com/google/leveldb/pull/1387) in the minimal, default-preserving
shape (the 1.23 global
RTTI-off ABI break broke Ceph and plyvel and forced distros to carry a
patch — see also [#927](https://github.com/google/leveldb/issues/927)). To
build without RTTI, pass `-DLEVELDB_DISABLE_RTTI=ON` **and** build all
consumers with RTTI disabled as well (e.g.
`-DCMAKE_CXX_FLAGS=-fno-rtti`); with only the library RTTI-off the link
fails by design, and the CI matrix pins both modes explicitly.

**Not included** (kept for the record):
[#755](https://github.com/google/leveldb/pull/755) (empty patch) and
[#760](https://github.com/google/leveldb/pull/760) (conflicts with current
code). [#850](https://github.com/google/leveldb/pull/850) is already fixed
upstream — `NewLogger()` opens with `fopen(..., "wN")`, which disables handle
inheritance. Context: [#526](https://github.com/google/leveldb/pull/526), the
earlier "comprehensive native Windows support" PR, was closed in 2019 when
Google landed its own Windows port based on Chrome's code.

### D. Tests and CI added by this fork (2026-11-05, extended 2026-11-07)

Everything below is original here — no upstream PR proposed it. Each row links
the introducing commit and the CI job that now guards it, so a claim can be
traced to code and to the check that keeps it true.

| Change | Commit | CI guard | Why it exists | What I did upstream |
|---|---|---|---|---|
| **[Parameterized `CURRENT` terminator matrix](https://github.com/takomitkm/leveldb/commit/3806b66)** — `TEST_P` over `LF` / `CRLF` / lone `CR` (accepted, terminator stripped before the manifest name is used) plus no-terminator / empty (rejected, and the database must survive the rejection) | [3806b66](https://github.com/takomitkm/leveldb/commit/3806b66) | `leveldb_tests` (`CurrentFileTerminator.*`) | The lone-`CR` case had zero coverage anywhere. Reverting `VersionSet::Recover()` to the upstream logic makes the `CRLF` and `CR` cases fail with the historically reported corruption messages (2 red / 3 green), so the matrix is calibrated rather than decorative | — |
| **[`TestRenameFileDurableSemantics`](https://github.com/takomitkm/leveldb/commit/9418880)** — read-back through an independent `CreateFileW` handle after rename, replace-in-place case, independent directory enumeration, not-found error path | [9418880](https://github.com/takomitkm/leveldb/commit/9418880) | `win-quick`; `build-and-test` windows/msvc | `TestRenameFileReplacesExisting` covered the #775 replace surface only; the durable/replace split was untested. `MOVEFILE_WRITE_THROUGH` persistence is recorded as deliberately untested (needs power-loss injection) and backed by the `MoveFileExW` contract | — |
| **[`win-quick` cell](https://github.com/takomitkm/leveldb/commit/6b2405e)** — the Windows-only suites (4 `EnvWindowsTest` cases plus `FileNameTest`/`Logging`) in their own job, every step asserting a minimum passed-count | [6b2405e](https://github.com/takomitkm/leveldb/commit/6b2405e) | itself | Windows regressions used to surface only after a full matrix round-trip (~15 min); now ~3 min. `ctest -R` exits 0 even when its regex matches nothing (verified on cmake 4.2.3), so a silent empty selection would otherwise look green | — |
| **[MSVC cells build with the static CRT (`/MT`)](https://github.com/takomitkm/leveldb/commit/86e42d4)** | [86e42d4](https://github.com/takomitkm/leveldb/commit/86e42d4) | `build-and-test` windows/msvc; `win-quick` | Under `/MD`, a host whose `MSVCP140.dll` predates the toolchain crashes the test binaries in the fixture constructor (`0xC0000005`) before a single case runs; `/MT` removes that environment dependency (219/219 green on such a host) | — |
| **[Fuzz regression-seed replay + crash-report artifact](https://github.com/takomitkm/leveldb/commit/a52543f)** | [a52543f](https://github.com/takomitkm/leveldb/commit/a52543f) | `fuzz` | Findings used to die in uploaded artifacts. `fuzz/regression_seeds.txt` is now replayed before every exploration run, so a fixed crash cannot silently return; failures ship a ready-to-edit seed line | — |
| **[`LEVELDB_DISABLE_RTTI` defaults to OFF](https://github.com/takomitkm/leveldb/commit/18b79bb)** | [18b79bb](https://github.com/takomitkm/leveldb/commit/18b79bb) | `build-and-test` all cells | With it ON, the library emits no `typeinfo` for polymorphic classes while consumer targets keep the compiler default, so linking fails (`typeinfo for leveldb::WritableFile`). Opt-in now, mirroring `ROCKSDB_DISABLE_RTTI` | — |
| **[Exceptions opt-out; flags scoped to the leveldb target](https://github.com/takomitkm/leveldb/commit/aa0c122)** | [aa0c122](https://github.com/takomitkm/leveldb/commit/aa0c122) | `no-exceptions` job; `build-and-test` all cells | Upstream hard-codes both switches and rewrites the global `CMAKE_CXX_FLAGS`, so leveldb cannot be built with RTTI and a consumer that uses `typeid` on leveldb types cannot link (upstream [#731](https://github.com/google/leveldb/issues/731)). Target-scoped flags make both switches available without breaking the Kyoto Cabinet benchmark | PR [#1387](https://github.com/google/leveldb/pull/1387) — the minimal variant; target scoping stays here |
| **Docs-only pushes and PRs skip CI** | [887fee5](https://github.com/takomitkm/leveldb/commit/887fee5) (push side), plus this commit (pull-request side) | all workflows | README/provenance edits were spending the full matrix; `paths-ignore` now covers `**.md`, `doc/**`, `LICENSE`, `AUTHORS` on both `push` and `pull_request` (see the note about required status checks in the workflow files) | — |
| **[Probe `ZSTD_compress`, and test the decompression path](https://github.com/takomitkm/leveldb/commit/a34d62a)** — the optional-backend probes were only ever checked by their consumers; `CompressionTableTest.RoundTripThroughDecompression` reads every value back through the block reader, and the new `optional-deps` job asserts all four `HAVE_*` probes, the linkage, and that the tests ran instead of skipping | [a34d62a](https://github.com/takomitkm/leveldb/commit/a34d62a) | `optional-deps` (Debug/Release, both codecs required) | `check_library_exists(zstd zstd_compress …)` linked a symbol libzstd never exported (it is `ZSTD_compress`), so `HAVE_ZSTD` stayed empty even with `libzstd-dev` installed — the zstd code path was never compiled, and the only test covering it `GTEST_SKIP`ped silently, keeping CI green (upstream `bb74ef7` has the same defect, so the unoverridden CMake probe also failed in that snapshot). A second, independent defect sat in the config header: `port_config.h.in` guarded its `#cmakedefine01 HAVE_ZSTD` with `#if !defined(HAVE_Zstd)`, so a caller's `-DHAVE_ZSTD` matched nothing and the generated header redefined it to the probe's 0 — the builds that tried to force zstd on were the ones that lost it. Both halves are carried here ([78dd46be](https://github.com/google/leveldb/commit/78dd46be97) by yshihao from [PR #1121](https://github.com/google/leveldb/pull/1121); [366c389c](https://github.com/google/leveldb/commit/366c389c67) by RaghunandanKumar from [PR #1333](https://github.com/google/leveldb/pull/1333)). The new test is calibrated: forcing `Zstd_Uncompress()` to return false makes the zstd parameter fail with "corrupted zstd compressed block contents" while the old offset-only test stays green. `GTEST_SKIP` exits 0, so the job fails on a skip rather than trusting the green | PR [#1385](https://github.com/google/leveldb/pull/1385) (probe + guard + test; the CI cell stays in this fork for now) |
| **[Path and custom-Env compatibility regressions](https://github.com/takomitkm/leveldb/commit/6b5edb6)** | [6b5edb6](https://github.com/takomitkm/leveldb/commit/6b5edb6) | `compatibility` | Non-ASCII file/logger/database paths are checked in ACP and UTF-8 modes; custom log readers reuse external buffers across full and short reads. Each old behaviour is restored temporarily to prove the new cases fail, then the fix is rebuilt and verified again | comment on [#1334](https://github.com/google/leveldb/pull/1334) |
| **[Actions-built binary releases and extracted-package consumers](https://github.com/takomitkm/leveldb/commit/b57269c)** — 22 binary variants, including both Windows path modes, installed and archived with exact-source build records | [b57269c](https://github.com/takomitkm/leveldb/commit/b57269c) | `release` | Packages are checked for all public headers, relocatable CMake exports, runtime/dependency settings and working consumers built from the final extracted archives. Tag pushes publish only after all groups and manifests pass validation | — |
| **[Per-fix regression tests for the 2026-11-07 wave](https://github.com/takomitkm/leveldb/commit/bf7212e)** — every fix in the table-C rows above ships with its own regression test (`BuilderTest` is a new suite registered in CMakeLists; the corruption, `ChangeOptions`, `ReadBlockIssue1349` and repair cases are named in each row's CI-guard cell), and each was verified red against the pre-fix commit before the fix landed | [bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e) through [527720c](https://github.com/takomitkm/leveldb/commit/527720c) | `leveldb_tests`; `win-quick` (`env_windows_test`); `optional-deps` | The tests exist because the whole wave passed the inherited suite green while carrying real defects — the suite knew nothing about any of these paths. Two details were only caught dynamically: the dangling `DBImpl*` in a first draft of the compaction test (debug SIGSEGV under gdb) and `ReadFile`'s kernel buffer probe (a short scratch with a 4 GiB request fails even when the fix is correct, so the test commits the contract-mandated 4 GiB scratch) | — |
| **[Read-path boundary contracts pinned](https://github.com/takomitkm/leveldb/commit/07e88e8)** — failed varint decodes leave input and output untouched, successful ones advance exactly past the encoding, redundant-continuation encodings stay accepted; `SIZE_MAX` handle sizes are rejected as wrapping and the largest non-wrapping size goes through the allocation-failure path | [07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8) | `leveldb_tests` (`Coding.Varint*`, `ReadBlockIssue1349Test.RejectsHandleSizeThatWrapsWithTrailer`, `…RejectsLargestNonWrappingHandleSize`) | The three read-path guards ([aa3b900](https://github.com/takomitkm/leveldb/commit/aa3b900), [fb5dc12](https://github.com/takomitkm/leveldb/commit/fb5dc12), [7d10a9a](https://github.com/takomitkm/leveldb/commit/7d10a9a)) shipped without targeted tests. The restart-offset boundary was closed separately — see the next row | — |
| **[The 4 GiB restart-offset boundary, closed with a real 4 GiB block](https://github.com/takomitkm/leveldb/commit/8c226e0)** — both boundary blocks built on one lazily-committed 4 GiB buffer (size U+5 with zero restarts → offset U+1 must come back `Corruption`; size U+4 → offset exactly U must stay accepted), judged by `NewIterator`'s status only: the uninitialized body is never iterated, so the test touches two pages and skips gracefully if the 4 GiB commit is unavailable | [8c226e0](https://github.com/takomitkm/leveldb/commit/8c226e0) | `leveldb_tests` (`BlockTest.RestartOffsetMustBeUint32Representable`) | Closes the gap the row above left open. Verified red against aa3b900's parent ([719f24b](https://github.com/takomitkm/leveldb/commit/719f24b): the offset truncated to 0 and the oversized block was accepted silently) and green here; note `NumRestarts() == 0` routes `NewIterator` to the empty iterator, which is what makes the two outcomes observable without decoding anything | — |

### Provenance, method, and scope — PR scan 2026-11-04 00:17, branch scan 2026-11-04 01:13 (UTC+8)

**Every patch in this tree comes from one of these places, and nothing else:**
the six open google/leveldb PRs named in the tables (#988, #1214, #1366,
#1334, #1336, #1368), one commit from bitcoin-core/leveldb-subtree
(`ad9b1c9`, the `file_size` initialization), two commits from Mojang's
Minecraft leveldb trees ([mojang/leveldb#8](https://github.com/Mojang/leveldb/pull/8)
— the CRLF fix — and [Amulet-Team/leveldb-mcpe@52253f2f](https://github.com/Amulet-Team/leveldb-mcpe/commit/52253f2f)
— the `RepairTable` parse guard), the OSS-Fuzz public-API harness adapted
into [`fuzz/fuzz_db.cc`](fuzz/fuzz_db.cc) (from
[google/oss-fuzz](https://github.com/google/oss-fuzz/blob/master/projects/leveldb/fuzz_db.cc)),
six things written for this fork
because no prior proposal exists anywhere: the durable `RenameFile`, the
appending `NewLogger`, the manifest parent-directory sync, the recovery
fuzzer `fuzz/fuzz_recovery.cc`, the
four regression tests, and this README audit — plus hardening fixes
adopted from unmerged PRs found through the issue scans: three from the
first pass ([#1350](https://github.com/google/leveldb/pull/1350),
[#1326](https://github.com/google/leveldb/pull/1326), [#1359](https://github.com/google/leveldb/pull/1359))
and eight more from the 2026-11-07 sweep ([#1335](https://github.com/google/leveldb/pull/1335),
[#1340](https://github.com/google/leveldb/pull/1340), [#1344](https://github.com/google/leveldb/pull/1344),
[#1354](https://github.com/google/leveldb/pull/1354), [#1367](https://github.com/google/leveldb/pull/1367),
[#1377](https://github.com/google/leveldb/pull/1377), [#1380](https://github.com/google/leveldb/pull/1380),
[#1382](https://github.com/google/leveldb/pull/1382), all in table C).

**Post-merge review (2026-11-07):** the eight backports above were audited
one commit at a time (independent read-only reviews plus a cross-commit
integration pass), which found error-path defects in four of them and four
baseline defects nearby. Six follow-up fixes were written for this fork
([bf7212e](https://github.com/takomitkm/leveldb/commit/bf7212e),
[9789dab](https://github.com/takomitkm/leveldb/commit/9789dab),
[0959c2f](https://github.com/takomitkm/leveldb/commit/0959c2f),
[4b6a977](https://github.com/takomitkm/leveldb/commit/4b6a977),
[edba255](https://github.com/takomitkm/leveldb/commit/edba255),
[527720c](https://github.com/takomitkm/leveldb/commit/527720c), table C)
and one platform-parity fix ([0a472d3](https://github.com/takomitkm/leveldb/commit/0a472d3),
table A); every one was verified to fail red against its pre-fix parent
before landing. Three of the backports keep their implementations unchanged
and got boundary-contract tests instead ([07e88e8](https://github.com/takomitkm/leveldb/commit/07e88e8),
table D); the restart-offset boundary test was later closed with a real
4 GiB block ([8c226e0](https://github.com/takomitkm/leveldb/commit/8c226e0),
table D), so every guard in the wave now has targeted coverage.

**Isolated verification against the upstream PR branches (2026-11-08):**
the four follow-up fixes above were re-verified on the community PRs'
own code, in isolation from this fork's other changes — for each of
[#1335](https://github.com/google/leveldb/pull/1335),
[#1340](https://github.com/google/leveldb/pull/1340),
[#1344](https://github.com/google/leveldb/pull/1344) and
[#1354](https://github.com/google/leveldb/pull/1354): google/leveldb
`main` + the PR applied verbatim + the regression tests reproduces each
defect (red: assert aborts and/or failed tests), and adding the
corresponding fix alone turns them green with the rest of the suite
passing. Two of this fork's guard-tests necessarily fail on a tree that
lacks their guards (`OversizedBlockHandle`,
`Harness.RejectsOverflowingEntryLengths` on the #1340 tree — their guards
live in other commits and are excluded there). The #1354 tree additionally
needs `-DHAVE_ZSTD=ON`: upstream's probe still looks for the lowercase
`zstd_compress` symbol and silently disables zstd (see
[a34d62a](https://github.com/takomitkm/leveldb/commit/a34d62a)), so the
PR's own zstd tests skip without it.

**How the Windows side was audited:** `env_posix.cc` and `env_windows.cc`
were read side by side; every behavioural difference found is either in
table A (fixed here), table B (documented, left open) or the "checked and
found consistent" list. If a Windows/POSIX inconsistency is not listed
there, this audit did not find it.

**Where PRs were searched** — every PR, open and closed, in each repository
below (PR scan finished 2026-11-04 00:17 UTC+8):

| Repository | Result of the PR scan |
|---|---|
| google/leveldb | the six open PRs merged above; #755 (empty patch), #760 (conflicts), #850 (already fixed upstream) checked and excluded, see below |
| pmmp/leveldb | one PR ever (#4, internal cleanup) — nothing usable |
| Mojang/leveldb | **#8 and #16 adopted (tables C and A)**. Their default branch `master` mirrors upstream exactly; the real work sits on `main`, which is 11 commits ahead. The whole commit list was reviewed plus all 18 PRs: apart from #8 (CRLF in `CURRENT`) and #16 (CMake RTTI/exception scoping, which answers upstream issue #731), the rest is Minecraft build/app infrastructure and tuning (zlib support, a host-app allocator, JSON stats, level-0 limit tweaks, seek-compaction switches, log-buffer sizing, a `Get(nullptr)` API relaxation) — usable as patches here, but Mojang-specific features rather than fixes |
| Amulet-Team/leveldb-mcpe | #6/#9/#10 are Minecraft-specific (CRLF/binary `CURRENT` handling, locking against Minecraft's own LOCK-ignoring clients) — only #6's defect is general, and it is covered by the Mojang #8 row; #11 "Crash Fixed" adds two blank lines and nothing else |
| firebase/leveldb | podspec/release management; the only code PR (#4) sprinkles C-style casts to silence warnings — no behaviour change |
| bitcoin-core/leveldb-subtree | surveyed at commit level (~40 commits on `bitcoin-fork`): the `file_size` initialization is the only useful non-upstream change; the rest is refactors, CI, C++ standard bumps and a breaking `GetName()` API addition |
| oodrive/leveldb.net | PRs are README edits and a WIP; its `RenameFile` workaround lives in its vendored C++ copy, not in a PR |
| danielgindi/leveldb | the #526 author's fork — no longer exists (404 as of this scan) |

**Branch-level scan** — every branch of every repository above was then
enumerated, dated by its latest commit, and compared against upstream `main`
(`ahead_by > 0` = unique work, reviewed commit by commit; branch scan
finished 2026-11-04 01:13 UTC+8). This is done per branch because a fork's
default branch can silently mirror upstream while the real work sits on
another branch — Mojang/leveldb is exactly that case (`master` = mirror,
`main` = their actual work). Results per repository:

- **google/leveldb** — its non-`main` branches (`reuse-manifest` from 2014,
  `doc-testing` from 2019) are stale experiments whose content landed on
  `main` long ago.
- **pmmp/leveldb** — 7 branches: `master` and `no-handle-inheritance` mirror
  upstream; `reuse-manifest` and `doc-testing` are byte-identical copies of
  upstream's own stale branches; `env_rename` re-implements the
  `Env::Remove{File,Dir}` API upstream already has; `mojang-compatible`
  (24 commits) and `zlib-length-prefix` (17) are the Mojang compressor
  framework (zlib/zstd/brotli/zopfli world formats) — Minecraft features,
  not fixes.
- **Mojang/leveldb** — the remaining 14 branches are the per-feature working
  branches behind the PRs reviewed above (plus `leveldb_mirror`/`rc` mirrors
  and the same 2011 Boost-era `windows` port firebase has) — nothing
  additional.
- **Amulet-Team/leveldb-mcpe** — 28 branches; this repo carries Mojang's
  *original* leveldb-mcpe tree, and its 27 pre-2021 branches share no git
  ancestry with upstream (judged against their own history instead). Two
  general fixes were found: the `RepairTable` parse guard (**adopted**, table
  C) and a 2017 variant of the CURRENT CRLF fix (third independent instance,
  table C). The rest is obsolete or Minecraft-specific: fixes to the removed
  `env_win`/`env_winrt` backends — including an event-handle-per-read leak
  and a `FormatMessageW` bytes-vs-chars fix, neither of which applies to
  upstream's Chrome port (no `CreateEvent`, `FormatMessageA` counts bytes
  correctly) — Windows condvar/pread rework, compressor formats
  (brotli/zstd/zopfli), Windows Phone/WinRT, build and CI work.
- **firebase/leveldb** — 6 branches: `master`'s only unique commit is an iOS
  bitcode build flag (2015), `android` is a 2011 NDK port, `windows` the
  2011 Boost port, `firebase-release` is packaging; nothing general.
- **bitcoin-core/leveldb-subtree** — one branch (`bitcoin-fork`), fully
  mined at commit level; re-confirmed, nothing new.
- **oodrive/leveldb.net** — not a GitHub fork (no common ancestry to
  compare); branches are `master` and a Copilot-generated WIP updating the
  vendored copy to 1.23 — no original C++ fixes.

**Issues** (two passes, 2026-11-04): pass 1 reviewed the highest-volume
issues (top 15 by comments, top 15 by reactions). Pass 2, after widening the
net, cross-referenced **all 700 issues that have any discussion** against
every PR ever filed (655) and deep-read the 14 unmerged PRs attached to the
29 candidate issues. Result: **three fixes adopted** — [PR #1350](https://github.com/google/leveldb/pull/1350)
(2 GiB out-of-bounds read in `DecodeEntry`, issue #1348), [PR #1326](https://github.com/google/leveldb/pull/1326)
(unbounded allocation via crafted block handles, issue #1318) and [PR #1359](https://github.com/google/leveldb/pull/1359)
(size_t underflow on short internal keys, issue #103); the other eleven
candidates were rejected — #1000 changes the public `Comparator` API,
#762/#763/#764 (4 GiB values) each fix only half the path, #771 is an
`Env` API redesign, #1031 adds a feature option, #815 is a one-line log
typo, #1374 lacks a test, #869 only touches `db_bench`, and #385 (pthread
detach) is obsolete — upstream's `StartThread` has been `std::thread` +
`detach()` for years. The first pass's verdicts stand for the rest: #401
was already fixed upstream (646c3588de84, in this tree's base), #783 and
#182/#1082 are design debates without a patch, #425 ended as an unresolved
caller-lifetime diagnosis, #1247 is a C++14 toolchain complaint. #195/#775
remain bug-report context only.

**CMake library detection under cross builds:** `check_library_exists()`
compiles its probe but never links it, so under a cross toolchain whose
sysroot lacks crc32c/snappy/zstd every `HAVE_*` turns into a false positive
and the build breaks. Known upstream discussion: [#1146](https://github.com/google/leveldb/issues/1146)
(the false-negative twin), [PR #809](https://github.com/google/leveldb/pull/809)
(FindSnappy module, open since 2020), and [#684](https://github.com/google/leveldb/issues/684)
(iOS cross build, closed as an iOS issue). This fork fixes it minimally:
the `HAVE_*` checks are cache variables a cross build pins explicitly
(`-DHAVE_CRC32C=OFF ...`), which is compatible with a future Find-module
approach.

Other
LevelDB implementations — RocksDB, goleveldb, Chromium's
`third_party/leveldatabase` — appear above only as prior-art evidence
in the downstream-workaround column, never as patch sources.

**Who made this:** the entire tree was assembled by AI coding agents and has
**not been human-reviewed**. Every provenance claim above is a link that can
be checked. Use at your own risk.

**To pick up new work later:** everything up to the 2026-11-04 branch scan
(01:13 UTC+8) has been through this process once, and the unmerged-PR sweep
was re-run on 2026-11-07 (eight more backports, table C, each then audited
per commit). A future pass only needs
to look at google/leveldb PRs with `is:pr created:>=2026-11-08`, re-run the
same PR + branch scan on the fork list above, and re-diff
`bitcoin-core/leveldb-subtree` and `Mojang/leveldb` `main` against upstream.

Use at your own risk; patches are offered as-is, in the hope they are useful
upstream.

### E. Backward compatibility audit / 向后兼容性审查 (2026-11-07)

**Status: incomplete; do not assume a drop-in replacement.** The first audit
compared fork [bf583d0](https://github.com/takomitkm/leveldb/commit/bf583d0de56029dbecc0e19a50c43db74b6882d0)
against upstream [bb74ef7](https://github.com/google/leveldb/commit/bb74ef739973a70ca9f0d90788a1da89b788c0ad).
The source baseline for this document's 2026-11-07 review was
[f11fe16](https://github.com/takomitkm/leveldb/commit/f11fe16d3bce0182e699978694fff14d8ca3dfad);
after the 2026-11-07 hardening wave (29 commits) the fork main is
[dc2fe63](https://github.com/takomitkm/leveldb/commit/dc2fe63c21be889948bc79b1397f07d15eb67c7c),
whose new compatibility surface is covered by checklist items BC-41–BC-48.
Several fixes that were previously on `ci/release-agg4` are now in main.
Their presence in source is not a claim that every package or downstream
configuration has passed validation. An existing release is not repaired by
a later source commit.

Source/API compatibility, binary ABI, path semantics, database/codec support,
build configuration and delivery packages must be checked separately. The
original audit found only commentary changes in the public headers; that
observation alone cannot certify any of the other layers.

| Item | Established evidence | Current source and remaining work |
| --- | --- | --- |
| Custom `SequentialFile` buffers | At bf583d0, the same valid WAL was read correctly by upstream but returned no record and **0 corruption callbacks** in the fork when `Read()` returned its own buffer. | [6b5edb6](https://github.com/takomitkm/leveldb/commit/6b5edb6) copies returned data before buffer reuse and is now in main. Recheck the component and full DB recovery; retain the calibrated tests. |
| Native-code-page Windows paths | On ACP **936**, the original GBK path opened through the narrow API; interpreting the same bytes as UTF-8 failed with **error 3**. | Main has ACP/UTF-8 variants and `LEVELDB_WINDOWS_UTF8_PATHS`. Source builds still default to UTF-8. A native-code-page default, requiring no old-caller changes, remains a proposed policy to evaluate. |
| LOG and temporary paths | In the same non-ASCII directory, UTF-8 `fopen` failed while `_wfopen` succeeded. `SanitizeOptions` can ignore logger failure. | 6b5edb6 unifies these paths through wide APIs. Validate the actual LOG, directory enumeration, environment paths and all shipped variants. |
| Old Windows archives | Examined local agg.1/2/3 ZIPs contained one **2535-byte file** named `include/leveldb` instead of the public header directory. The agg.3 MSVC DLL Release archive matched the remote asset digest. | [b57269c](https://github.com/takomitkm/leveldb/commit/b57269c) installs and checks complete packages. Old archives remain historical failures; validate each new extracted asset rather than assuming the new scripts fixed every published file. |
| MSVC runtime across DLL boundaries | Passing C++ objects across DLL/client boundaries requires compatible runtime and ABI settings. The older blanket `/MT` guidance is insufficient for DLL consumers. | [f11fe16](https://github.com/takomitkm/leveldb/commit/f11fe16) adjusts shared-runtime handling for DLL consumers. Test actual compiler, runtime, allocation, Debug/Release and loader combinations before claiming binary interchangeability. |
| 2026-11-07 hardening wave ([dc2fe63](https://github.com/takomitkm/leveldb/commit/dc2fe63c21be889948bc79b1397f07d15eb67c7c)) | Backported strictness and error paths: bounded decompression pre-validation plus `nothrow` allocations ([PR #1354](https://github.com/google/leveldb/pull/1354)), restart-offset/handle-size/varint rejection ([#1367](https://github.com/google/leveldb/pull/1367), [#1380](https://github.com/google/leveldb/pull/1380)), `UINT64_MAX` `next_file_number` ([#1377](https://github.com/google/leveldb/pull/1377)), `ChangeOptions` invalid-call rejection ([#1340](https://github.com/google/leveldb/pull/1340) + follow-up), compaction/`BuildTable` fail instead of drop, repair metadata recompute and empty-table archiving, Windows ≥4 GiB chunked reads, directory-wide `_HAS_EXCEPTIONS=0` on MSVC. Public headers remain comment-only. | Valid-data invariance, new error visibility and the new test guards are covered by **BC-41–BC-48** in the checklist. The zstd cases of `ReadBlockIssue1349Test` only execute in the `optional-deps` job — verify with a real Snappy/Zstd environment and treat skips as untested. |
| Directory sync and rename | A temporary directory allowed child creation/rename but rejected `GENERIC_WRITE` directory open with **error 5**; a normal directory opened and flushed. Rename visibility tests do not simulate power loss. | The permission requirement remains. Decide the supported filesystem/permission scope; do not silently turn a failed durability operation into success. SMB and physical power-loss behaviour remain unverified here. |
| Compression capability | Packages without Snappy/Zstd cannot decode existing blocks using those codecs. Fixing the Zstd probe makes explicit Zstd selection effective. | Check upgrade and downgrade with real compressed blocks and verified decoder capabilities. Built-in CRC32C remains available when the external CRC32C library is disabled. |
| Other deliberate changes | Corrupt-input checks, incomplete-tail recovery, CRLF/CR acceptance, best-effort repair, direct logger append, clock precision and build flags can change observations. | Test the documented contract. Do not reintroduce out-of-bounds reads, unsafe tail reuse or destructive logger behaviour merely to match historical bugs. No complete ABI or downstream compatibility certification is supplied. |

**Library-side path policy under consideration:** default to the original
native file-API code page, convert internally to UTF-16 and use wide APIs
consistently. Existing native-code-page callers would need no changes. Keep
an explicit UTF-8 variant for existing UTF-8 callers. The default mode would
retain upstream's code-page limitations; UTF-8 callers would need the matching
variant. Do not guess between two valid encodings or switch encodings after
not-found errors. No path-policy code change is made by this documentation.

**Execution plan:** [超详细向后兼容性测试与修复清单](docs/BACKWARD_COMPATIBILITY_CHECKLIST.zh-CN.md)
records version baselines, dependencies, fixtures, per-item reproduction steps,
expected results, causes, repair options, necessity decisions and release gates.
All future execution boxes start unchecked. Earlier component/WinAPI evidence
is identified separately from work not yet performed.


### F. Windows directory flush: who does it, who skips, and what it costs (2026-11-14)

The `30b34e0` row in table A is the one change here whose core claim is durability rather
than bytes: Windows `Sync()` only flushed the manifest file, so a newly created manifest's
directory entry could be lost. This section carries the two things that row needs but should
not hold inline: what the extra flush costs, and whether anything outside the LSM/DB lineage
does it.

**Cost (measured on Windows, 2026-11-14).** Two cross-built `db_bench.exe` arms from this
fork (MinGW 13 in WSL): arm A as shipped, arm B with `SyncDirIfManifest()` returning OK,
identical trees otherwise. Both arms instrument `VersionSet::LogAndApply` (MANIFEST sync
count) and `SyncDirIfManifest()` (per-call `steady_clock`), so the added cost is measured
directly instead of inferred from noisy end-to-end throughput.

| fillrandom, 16 B keys / 100 B values | MANIFEST syncs | directory flush total | share of wall time |
|---|---|---|---|
| 2 M ops, `write_buffer_size=4 MB` | 155 | 551 ms / 16.7 s | 3.3 % |
| 2 M ops, `write_buffer_size=1 MB` | 413 | 275 ms / 19.8 s | 1.4 % (idle box); 1.33 s / 4.6 % on a loaded one |
| 1 M ops, `write_buffer_size=256 KB` | 704 | 2.4–2.8 s / 28 s | 8.5–8.8 % (A/B throughput 10.7 % slower: signal above noise) |

The cost is the MANIFEST `Sync()` count times one directory flush. That count is one per
`LogAndApply` — every flush and compaction — and one flush measured 0.66–3.5 ms depending on
writeback pressure (ctypes on the same sequence: 266 µs p50 on a clean directory, 765 µs p50
right after a create; a 5 MB file flush is 951 µs for scale). With the default 1–4 MB write
buffer the overhead is 1–3 %; only a 256 KB buffer approaches 9 %. End-to-end A/B throughput
at the 1 % level is not resolvable on this machine (runs produced both signs), which is why
the table reports the instrumented timer.

**Survey: who flushes a directory on Windows.** Method: source reading plus two local probes —
`CreateFileW` + `FlushFileBuffers` via ctypes, and `fs.openSync(dir, 'r+')` + `fsyncSync` in
Node 22.22.2. The decisive detail is handle access: a directory opens with `GENERIC_READ`, but
`FlushFileBuffers` on that handle fails with `ERROR_ACCESS_DENIED`; it succeeds with
`GENERIC_WRITE`, no administrator rights. Most runtimes and `open(2)`-shaped APIs open
directories read-only, which is why the call "does not work" for them.

*DB lineage — all skip.* SQLite
([`os_win.c`](https://github.com/sqlite/sqlite/blob/master/src/os_win.c#L4555): the `syncDir`
parameter is "Not used on win32"); LMDB (write-through flags, no directory sync); RocksDB
([`io_win.cc`](https://github.com/facebook/rocksdb/blob/main/port/win/io_win.cc#L1085):
`WinDirectory::Fsync()` returns OK); Pebble
([`default_windows.go`](https://github.com/cockroachdb/pebble/blob/master/vfs/default_windows.go#L43):
`windowsDir.Sync()` returns nil, "same behavior as RocksDB"); Chromium's filesystem service
([`directory_impl.cc`](https://github.com/chromium/chromium/blob/main/components/services/filesystem/directory_impl.cc#L253):
`DirectoryImpl::Flush()` is `#if !BUILDFLAG(IS_WIN)`, "On Windows no need to sync
directories"); PostgreSQL
([`fd.c`](https://github.com/postgres/postgres/blob/master/src/backend/storage/file/fd.c#L3848):
opens directories `O_RDONLY` with CRT `open()`, which fails on Windows with EACCES, so the
error is ignored). One SQLite derivative was checked and found to introduce **no** new gap:
libSQL (Turso) bundles SQLite 3.47.0 as `sqlite3.c` (its POSIX `DIRSYNC` machinery intact) and its
custom `libsql_wal` VFS only delegates to SQLite's WAL — a repo-wide search finds no
libSQL-authored rename-over commit path that would bypass the core, so the POSIX directory flush is
inherited unchanged and no Windows-specific gap is added.

*Outside the DB lineage — four independent implementations do it.*
[deltabox-lite](https://github.com/delta-box/deltabox-lite/blob/main/crates/deltabox-core/src/windows/durability.rs)
(agent sandbox with commit semantics) opens the directory with
`FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH` and
flushes every parent up to the root;
[ejm-rightkit-fs](https://github.com/ejmlabs/ejm-rightkit-fs/blob/main/src/atomic.rs#L1439)
(atomic-replace library) documents exactly the access rule measured above — "a handle opened
for listing cannot be flushed (`ERROR_ACCESS_DENIED`), so this opens `GENERIC_WRITE` with
`FILE_FLAG_BACKUP_SEMANTICS`";
[whim](https://github.com/carthage-software/whim/blob/main/src/filesystem.rs#L211) (a
programming language) uses `.write(true).custom_flags(FILE_FLAG_BACKUP_SEMANTICS)` +
`sync_all()`;
[D2rHub](https://github.com/gjy991229/D2rHub/blob/main/src-tauri/src/infrastructure/durable_fs.rs)
(a Tauri application) uses the same sequence. The primitive is also reachable from Node:
libuv adds `FILE_FLAG_BACKUP_SEMANTICS` to every open
([`fs.c`](https://github.com/libuv/libuv/blob/v1.x/src/win/fs.c#L607)), so
`fs.openSync(dir, 'r+')` + `fs.fsyncSync` succeeds (2.73 ms measured) while `'r'` returns
EPERM.

*Outside the DB lineage — the skippers, and four contradictory reasons.* restic
([`local_windows.go`](https://github.com/restic/restic/blob/master/internal/backend/local/local_windows.go):
`fsyncDir` is a no-op, "Can't explicitly flush directory changes on Windows"); libgit2
([`futils.c`](https://github.com/libgit2/libgit2/blob/main/src/util/futils.c#L1205):
`git_futils_fsync_dir` is `return 0` under `GIT_WIN32`);
[youta](https://github.com/vitaly-zdanevich/youta/blob/main/src/durability.rs) believes
metadata flushing needs a volume handle and administrator rights;
[libfreemkv](https://github.com/freemkv/libfreemkv/blob/main/src/io/fsync/windows.rs)
believes Rust's `std` cannot open a directory;
[unmv](https://github.com/mevanlc/unmv/blob/main/src/platform/windows.rs#L984) says there is
"no generally supported directory fsync equivalent";
[kit](https://github.com/speakeasy-api/kit/blob/main/src/resilient_fs/backend.rs#L671) says
"Windows does not support FlushFileBuffers on directory handles"; **FrankenSQLite** ([`windows.rs`](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-vfs/src/windows.rs#L1564-L1580):
`sync_parent_directory` is an explicit `Ok(())` no-op, "Win32 does not expose a portable
fsync-directory operation … Keep the namespace hook an explicit no-op rather than attempting
FlushFileBuffers on a directory handle (unsupported on common filesystems and liable to return
ERROR_INVALID_HANDLE)") and [`backup.rs`](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-core/src/migration/backup.rs#L428-L435)
(`sync_directory` fsyncs the parent on `#[cfg(unix)]`, no-ops on `#[cfg(not(unix))]`, "there is
no portable directory fsync … not a power-loss proof"). The project is a from-scratch Rust
reimplementation of SQLite (not a fork), supports Windows as a first-class target, and its
commit/publication path uses `std::fs::rename` for atomic replacement — so the Unix path
[`epoch.rs`](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-core/src/epoch.rs#L890-L892) does fsync the parent after the rename while the Windows path silently skips it, the same gap the rest of this section documents. None of the skip reasons above matches the
two probes: the call works, non-admin, with `GENERIC_WRITE`.

**What this means for `30b34e0`.** The same
`GENERIC_WRITE + FILE_FLAG_BACKUP_SEMANTICS + FlushFileBuffers` sequence has been
independently reinvented by four non-DB projects, one of which recorded the same
`ERROR_ACCESS_DENIED` pitfall — and by etcd, a distributed database, whose Windows file
helper has opened a directory "with write access for syncing" since 2017 and, since 2026-08,
actually fsyncs that handle ([section G](#g-why-nobody-did-it-the-stated-reasons-verbatim-2026-11-15));
"Windows cannot sync a directory" is inherited folklore, not a
platform limit. Renames do have an official write-through flag
(`MoveFileExW(MOVEFILE_WRITE_THROUGH)`, which this fork uses in `RenameFile`); a *newly
created* file's directory entry has no equivalent per-file flag, which is the gap leveldb's
manifest creation falls into — and why projects that rely on rename flags never needed the
directory flush.

**Wording lineage (added 2026-11-14, from the PR [#1388](https://github.com/google/leveldb/pull/1388) research).** The first
written rationale found anywhere is Chromium's filesystem service, 2017-06-16
([0c2987eee0](https://github.com/chromium/chromium/commit/0c2987eee0), bug 732863): "On Windows no need to sync directories.
Their metadata will be updated when files are created, without an explicit sync." — justified there by "opening the directory
as a file to then call Flush on it ... might cause this method to always fail on windows" (a read-only handle fails with
`ERROR_ACCESS_DENIED`). The falsifiable claim entered leveldb with the pre-merge Windows-support PR
[#526](https://github.com/google/leveldb/pull/526) (2017-11), whose `SyncDirIfManifest()` body stated "you cannot call
FileFlushBuffers() or any other data syncing routine" on a directory handle — false: the `GENERIC_WRITE` sequence works
(the two probes above); ghemawat's [2018-02-02 review](https://github.com/google/leveldb/pull/526#discussion_r165759738)
asked to "delete it and move the comment into Sync", and the landed 2019 port ([c69d33b0](https://github.com/google/leveldb/commit/c69d33b0),
based on Chrome's code) carries the softened "no need" form quoted in table A. The practice of skipping predates all of
this (SQLite's `os_win.c`, RocksDB's 2015 port).

**Status update (added 2026-11-14, hours after this survey):** restic
([PR #22177](https://github.com/restic/restic/pull/22177)) and kit
([PR #345](https://github.com/speakeasy-api/kit/pull/345)) now have open
pull requests implementing exactly this sequence, and a fifth project
surfaced: [apache/maka#3898](https://github.com/apache/maka/issues/3898)
(5.9k stars) proposes the same for its storage layer. The rows above
record the state before those PRs. The precise framing is three-layer —
the capability and the NTFS-level behavior are documented, the Win32
persistence contract is not.

**Status update (added 2026-11-15):** FrankenSQLite now has an open draft
pull request fixing the two gaps recorded above —
[PR #539](https://github.com/Dicklesworthstone/frankensqlite/pull/539). It adds
two helpers with distinct semantics: `host_fs::sync_directory_handle(directory)`
flushes the given directory itself (Unix opens it read-only and fsyncs — the
original upstream behavior; Windows makes a best-effort directory-handle flush),
and `host_fs::sync_parent_directory(file_path)` parents the file and calls the
former. The Windows VFS rename-over hook and the migration-backup paths call
the appropriate one instead of their Windows no-ops. The original upstream Unix
backup helper was already correct; an earlier single-helper revision of the PR
introduced the off-by-one level error, which the split fixes. Windows has no
published Win32 contract that a directory-handle flush persists a namespace
entry, so the PR describes it as a measured NTFS best-effort attempt, not a
POSIX `fsync` equivalent or a crash-durability guarantee, and only NTFS was
tested. After a review caught the parent-vs-self confusion, the split was made
and the Windows target was compile-checked (`cargo check --target
x86_64-pc-windows-gnu`, red-green Iron Law) with a real-Windows runtime probe;
the upstream Windows CI suite has not run yet, so the PR stays a draft.

**Block-command tracing (added 2026-11-14): E14/E15.** The QEMU rig that could not
produce data for E13 was replaced by one that can: the data disk runs under
`blklogwrites`, so every `WRITE`/`FLUSH`/`FUA` the guest issues is recorded, and the
power-cut instant is chosen offline (replay cuts the log at the last barrier). Six
arms ran on WinPE and again on a full Windows 10 Pro 22H2 guest (three repetitions
each): create+write+close, `+FlushFileBuffers(file)`, `+directory-handle flush`,
plain rename, rename with `MOVEFILE_WRITE_THROUGH`, and create with
`FILE_FLAG_WRITE_THROUGH`. Results, identical on both stacks: the rename with
`MOVEFILE_WRITE_THROUGH` issues **no flush and no FUA** and its directory entry does
not reach the device before the call returns — indistinguishable from the plain
rename; `FlushFileBuffers(file)` issues a real barrier that covers the data but the
directory entry still does not reach the device; the directory-handle flush is the
only observed operation that puts the namespace change on the device (the
`go.txt`/source-file entries appear on the platter only in the arms that call it).
Real-hardware power loss and `ntfs.sys` replay behavior remain untested.

**Status update (added 2026-11-17, later): E21 measures the same call at the device
layer on a real Windows kernel.** A GitHub Actions `windows-latest` runner (real
`ntfs.sys`, virtual disk) ran four cumulative arms — no flush, `+FlushFileBuffers(file)`,
`+FlushFileBuffers(parent dir)`, `+FlushFileBuffers(volume root)` — with the measurement
window containing **only the flush calls**, 100 repetitions each, counting
`DiskIo/FlushBuffers` and `Write` events. The parent-directory flush issues **exactly one
disk flush IRP per call, the same +1.00/call as the volume-root flush**, so the call is
*not* a no-op at the storage stack and MS-FSA §2.1.5.7 <80> is right that a directory
flush exists. What it flushes, however, is journal-only and half-covered: journal-signature
attribution of the E17 logs shows the parent-dir flush pushes ~8 `$LogFile` records
per rep with only half inside the durable prefix, and **zero** MFT-record or
directory-index pages; the write count barely moves (**≈0.1 writes/call, against
≈1.3–3.1 for the root flush**, four runs). So the log record for the create may be
partially durable while the metadata it describes is not on the platter - whether
real `ntfs.sys` recovery would resurrect the entry from the log is the open
Tier-2 question. The conclusion for
`SyncDirIfManifest()` is unchanged — without a volume-root flush the namespace change is
not durable — but the mechanism is now precise: the flush is real, the metadata writeback
is missing. Measurements and the consistency table:
[docs/powercut/E21-RESULTS.md](docs/powercut/E21-RESULTS.md).

**Cost of the fix (added 2026-11-18): E21 measured the single call, E23 amortized
it.** A single volume-root flush costs p50 3.8–4.3 ms (14–16× the file flush);
the parent-directory flush is cheaper than the file flush (0.16–0.21 ms) because
it carries no metadata to write. Amortized over a real commit sequence
(`bench-sqlite-commit.ps1`, 200 transactions per variant, runner volume D:): the
parent-dir flush is **free** (−0.016 ms/commit, noise), the volume-root flush adds
**+1.47 ms/commit** (≈2×, TPS 698→344). For leveldb — one `SyncDirIfManifest()`
per 1–4 MB `LogAndApply` — that is ~1 ms/MB, acceptable. For SQLite, which pays
once per transaction, it doubles the commit cost — that is the B1/B2 decision.
Only the deltas are the signal; the runner is Azure storage and the simulator
omits SQLite parsing/B-tree/locking. E23 results: the E23 section of
[docs/powercut/E21-RESULTS.md](docs/powercut/E21-RESULTS.md).

**Real-build confirmation (added 2026-11-18, E24/E25).** E24 builds real
SQLite three ways (vanilla / dir-sync / dir+root-sync) and runs real
transactions: the dir flush stays free (+0.06 ms/txn), the dir+root shape
costs **+5.4 ms/txn in DELETE+synchronous=FULL (≈5.2×, TPS 795→150)**,
doubles exactly under synchronous=EXTRA, costs nothing in WAL mode, and
+35–50% on a mixed speedtest1 workload — so the honest per-flush price
brackets at 1.5–5.4 ms depending on how much metadata each flush has to
push. E25 builds real LMDB three ways: its dir-flush patch fires only at
env_open/env_copy, so dir+root adds ≈2.5–3 ms **per freshly created
environment** and the commit path is measurably untouched (phase B equal
across all three variants). All rigs green (SQLite probe red/green with an
exact hook counter; LMDB probe emits `DIRSYNC-PROBE-ROOT root_err=0` as a
non-admin on the runner). Report: [docs/powercut/E24-RESULTS.md](docs/powercut/E24-RESULTS.md).

The complete rigs are archived in this repository under
[docs/powercut/](docs/powercut/README.md) — the Linux QEMU power-cut model
(E11; the "5/5 vs 8/8" figures, which are Linux evidence, not Windows), the
E14/E15 Windows tracing rig (probe, launcher, log decoder, cut selection,
autopsy, and the full result report `E14-RESULTS.md`), and the E17 probe
that reproduces each database's patch shape (leveldb / SQLite / LMDB /
PostgreSQL / MySQL / FrankenSQLite) with its results
([E17-RESULTS.md](docs/powercut/E17-RESULTS.md)).

**Status update (added 2026-11-17): E17 revises the attribution above.** E17
ran sixteen arms on the same rig, including controlled variants that flush
different objects. A single `FlushFileBuffers` on the **parent directory** —
the exact sequence this fork's `SyncDirIfManifest()` and the five database
patches perform — produced **zero device-level metadata writes** (arms
1-5, 7-8, 13: the file's data is barriered, its directory entry stays in
memory). The platter effect E14 observed comes specifically from
`FlushFileBuffers` on the **volume root** (arm 11: a root flush alone
persists the file; arm 13: flushing the parent directory twice does
nothing; arm 14: rename followed by parent+root flush persists the new
name; arm 15: a durable delete followed by parent+root flush persists the
deletion). The likely mechanism is that ntfs.sys treats a root-directory
handle flush as a volume-level flush, while a subdirectory handle flush
covers only that directory's own streams. All E17 findings are WinPE/TCG/IDE
specific; real hardware is untested. (Root-flush cost was later measured:
E21 single call 3.8–4.3 ms p50, E23 amortized +1.47 ms per SQLite-shaped
commit — see the cost paragraph above.)

### G. Why nobody did it: the stated reasons, verbatim (2026-11-15)

Thirty-six projects were checked. Twelve have a directory-flush hook that runs on Windows and does nothing — leveldb among them, and the one whose skip is currently being fixed upstream; ten have no Windows directory operation at all; thirteen more repeat the same sentences in a phrase sweep (part C); and one — etcd — does it, and does it the way this fork does. Quoted exactly, at the commit each quote was read at.

**A. The hook exists on Windows and does nothing**

| Project | What the code says | Where |
|---|---|---|
| **SQLite** | `int syncDir /* Not used on win32 */`, then `UNUSED_PARAMETER(syncDir);` — no reason given | [os_win.c L4552–4563](https://github.com/sqlite/sqlite/blob/c5f86d0198519e70265ceaa3dbbfd14b283f3479/src/os_win.c#L4552-L4563) |
| **RocksDB** | `IOStatus WinDirectory::Fsync(...) { return IOStatus::OK(); }` — no stated reason. The call site is unconditional: `dir->FsyncWithDirOptions(IOOptions(), nullptr, DirFsyncOptions(manifest_path))` | [io_win.cc L1085–1088](https://github.com/meta/rocksdb/blob/6eeb6d11f96f878a3def51c8945b5fe8a3b483ca/port/win/io_win.cc#L1085-L1088) · [version_set.cc L7249–7250](https://github.com/meta/rocksdb/blob/6eeb6d11f96f878a3def51c8945b5fe8a3b483ca/db/version_set.cc#L7249-L7250) |
| **libgit2** | `#ifdef GIT_WIN32` → `GIT_UNUSED(path); return 0;` — no stated reason | [futils.c L1205–1211](https://github.com/libgit2/libgit2/blob/42a89d7cc9374a19e72fa57011e5fe3a99c7b3bf/src/util/futils.c#L1205-L1211) |
| **Pebble** | "Silently ignore Sync() on Windows. This is the same behavior as RocksDB." | [default_windows.go L43–47](https://github.com/cockroachdb/pebble/blob/a995e890c06ef86c583927906e16e7f313083a3f/vfs/default_windows.go#L43-L47) |
| **Chromium** | "On Windows no need to sync directories. Their metadata will be updated when files are created, without an explicit sync." | [directory_impl.cc L253–256](https://github.com/chromium/chromium/blob/bc619944d74c8acd9683523c6b4b48fd34591b04/components/services/filesystem/directory_impl.cc#L253-L256) |
| **leveldb** | "On Windows no need to sync parent directory. Its metadata will be updated via the creation of the new file, without an explicit sync." — the comment sits inside `Sync()`, so the skip is stated as fact, not as a limitation | [util/env_windows.cc L312–314](https://github.com/google/leveldb/blob/29465588d3dfdc80fba5e198037582b8fdb5dd60/util/env_windows.cc#L312-L314) |
| **PostgreSQL** | "Some OSs don't allow us to open directories at all (Windows returns EACCES), just ignore the error in that case." | [fd.c L3868–3875](https://github.com/postgres/postgres/blob/81b2179b492e3af891544813b7e22a42386b34d0/src/backend/storage/file/fd.c#L3868-L3875) |
| **restic** | "Can't explicitly flush directory changes on Windows." | [local_windows.go L9–10](https://github.com/restic/restic/blob/c734245fd3ae9bcac8ab3aaafb4482d4b5d0ae92/internal/backend/local/local_windows.go#L9-L10) |
| **BadgerDB** | "Windows doesn't support syncing directories to the file system." — the whole Windows implementation is `func syncDir(dir string) error { return nil }`, and the comment points at the 2019 issue where the question was first asked with a C program. The POSIX side does the real thing (`f.Sync()` on the opened directory), and `syncDir()` is called exactly where leveldb's gap is — right after the MANIFEST file is created | [dir_windows.go L98–100](https://github.com/dgraph-io/badger/blob/78b99b8b0f014b539455186404697a0ff58c55f2/dir_windows.go#L98-L100) · [dir_unix.go L93–108](https://github.com/dgraph-io/badger/blob/78b99b8b0f014b539455186404697a0ff58c55f2/dir_unix.go#L93-L108) · [manifest.go L299](https://github.com/dgraph-io/badger/blob/78b99b8b0f014b539455186404697a0ff58c55f2/manifest.go#L299) |
| **Lucene** (Apache, the search library behind Elasticsearch) | `IOUtils.fsync(fileToSync, isDir)` returns early on Windows: "opening a directory on Windows fails, directories can not be fsynced there" — the check is `if (isDir && Constants.WINDOWS)`, and the call site is `FSDirectory.syncMetaData()` | [IOUtils.java L522–533](https://github.com/apache/lucene/blob/781025d6fe32641b495c9dab2f43525f464f324d/lucene/core/src/java/org/apache/lucene/util/IOUtils.java#L522-L533) · [FSDirectory.java L268–274](https://github.com/apache/lucene/blob/781025d6fe32641b495c9dab2f43525f464f324d/lucene/core/src/java/org/apache/lucene/store/FSDirectory.java#L268-L274) |
| **Cassandra** (Apache) | `SyncUtil.trySyncDir(File)` calls `NativeLibrary.tryOpenDirectory()`; on Windows the JNA `open()` of a directory fails, the error is swallowed, the fd comes back as −1 and `trySync(fd)` returns immediately — a silent no-op. The stated reason is not about directories at all: `NativeLibrary` logs "the current operating system, windows, is unsupported by Cassandra" | [SyncUtil.java L117–125](https://github.com/apache/cassandra/blob/2249e37b51af9fd2a72865bbb56c1197e0d0d1ed/src/java/org/apache/cassandra/utils/SyncUtil.java#L117-L125) · [NativeLibrary.java L144–149](https://github.com/apache/cassandra/blob/2249e37b51af9fd2a72865bbb56c1197e0d0d1ed/src/java/org/apache/cassandra/utils/NativeLibrary.java#L144-L149) · [L325–328](https://github.com/apache/cassandra/blob/2249e37b51af9fd2a72865bbb56c1197e0d0d1ed/src/java/org/apache/cassandra/utils/NativeLibrary.java#L325-L328) |
| **FrankenSQLite** | "Win32 does not expose a portable fsync-directory operation. SQLite's own Windows VFS establishes journal create/delete durability through FlushFileBuffers on the journal handle … Keep the namespace hook an explicit no-op rather than attempting FlushFileBuffers on a directory handle (unsupported on common filesystems and liable to return ERROR_INVALID_HANDLE)." — a from-scratch Rust reimplementation of SQLite (not a fork); the Unix commit path fsyncs the parent after a `std::fs::rename` ([epoch.rs L890–892](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-core/src/epoch.rs#L890-L892)), while the Windows hook is the no-op quoted here, so Windows silently loses the directory entry a rename-over commit relies on | [windows.rs L1564–1580](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-vfs/src/windows.rs#L1564-L1580) · [backup.rs L428–435](https://github.com/Dicklesworthstone/frankensqlite/blob/eb5782e1a1e6270e94b3e614e1455384db1e4f64/crates/fsqlite-core/src/migration/backup.rs#L428-L435) |

Sorted by what the reason actually claims:

1. **No reason** — SQLite, RocksDB, libgit2. The parameter or call is simply dropped.
2. **Deference** — Pebble points at RocksDB; the chain ends in group 1.
3. **"Not needed"** — Chromium and leveldb say the OS persists directory metadata for you.
4. **"Impossible"** — PostgreSQL, restic, BadgerDB and Lucene say the platform cannot do it; Cassandra says the platform is not supported.

These are different implementation choices and assumptions, not mutually exclusive explanations. "The OS persists the directory metadata for you" and "an application cannot flush a directory" can both hold at the same time: the first is about what the OS does on its own schedule, the second is about what the API exposes to an application. What the measurements settle is only the categorical form of group 4 — a directory handle opened with `GENERIC_WRITE` + `FILE_FLAG_BACKUP_SEMANTICS` accepts `FlushFileBuffers` without administrator rights on NTFS, and the block-level tracing in section F shows the namespace entry reaching the device only in that arm. That contradicts "cannot" in the tested environment; it does not make the sequence a documented, supported Win32 application contract, nor portable across filesystems and Windows versions.

PostgreSQL's version is the narrowest and partly true — its own `open()`-based path does fail with EACCES on Windows — but the step from "our open fails" to "directories cannot be synced" is the same leap the others make.

**The folklore has a paper trail.** BadgerDB's Windows no-op cites the 2019 issue where the same question was asked with a C program: on a local directory the flush succeeded, on an SMB share it failed with `ERROR_INVALID_FUNCTION`, and the reporter's own conclusion was that the behavior was "not API defined, and thus not guaranteed to succeed" ([#699](https://github.com/dgraph-io/badger/issues/699) — and the comment the code points at, [504133587](https://github.com/dgraph-io/badger/issues/699#issuecomment-504133587)). WASI ran into the same wall in a specification, in 2023: "on Windows, there's `FlushFileBuffers` for syncing files to storage, but it only works on files. There doesn't appear to be a way to do this for directories", with the open question of whether a Windows implementation should silently succeed or fail ([WebAssembly/WASI#756](https://github.com/WebAssembly/WASI/issues/756), still open). The access-right split was reproduced independently in 2026 — a `GENERIC_READ` handle fails with error 5, a `GENERIC_READ | GENERIC_WRITE` handle flushes ([luth.io, 2026-08-06](https://luth.io/blog/2026/08/ntfs-atomic-file-replacement-not-rename-fsync/)). What is still missing everywhere is a document that says which of it an application may rely on.

The leveldb comment is the most-repeated sentence in the set, and its history is worth recording: Chromium wrote the original in 2017 ("On Windows no need to sync directories…"), the 2019 leveldb port was built on Chromium's code and carried a near-verbatim descendant, and upstream's own reviewer had asked in 2018 to "delete it and move the comment into Sync" — which is exactly where it sits today. The claim has been copied forward for eight years without anyone checking it.

**B. No Windows directory operation at all**

| Project | What the code does | Where |
|---|---|---|
| **LMDB** | The Windows build maps file-level durability to `MDB_FDATASYNC(fd) = (!FlushFileBuffers(fd))`. There is no directory fsync anywhere in the library, on any platform — no `fsync(dir…)`, no directory handle, no equivalent. | [mdb.c L398](https://github.com/LMDB/lmdb/blob/74dd6c3ec648c31857fe0a100f11c2a34619e2d7/libraries/liblmdb/mdb.c#L398) · [L2918–2919](https://github.com/LMDB/lmdb/blob/74dd6c3ec648c31857fe0a100f11c2a34619e2d7/libraries/liblmdb/mdb.c#L2918-L2919) |
| **MySQL / InnoDB** | `os_parent_dir_fsync_posix()` and all six of its call sites (L2952, L3160, L3218, L3257, L3273, L3300) sit inside the `#ifndef _WIN32` branch of os0file.cc that starts at L2026 — the Windows build compiles none of it. The POSIX version opens the parent with `::open(parent_dir, O_RDONLY)` and calls `::fsync(dir_fd)`, i.e. exactly the call that cannot work on Windows. | [os0file.cc L2831–2864](https://github.com/mysql/mysql-server/blob/06bcb0eb0ad281e08dd2b4d81b99ad25491433e7/storage/innobase/os/os0file.cc#L2831-L2864) · [call site L2952](https://github.com/mysql/mysql-server/blob/06bcb0eb0ad281e08dd2b4d81b99ad25491433e7/storage/innobase/os/os0file.cc#L2952) |
| **CouchDB** | `couch_file:sync/1` opens the file with `[append, raw]` and calls `fsync(Fd)` — file-level only. The tree contains no directory-sync call at all (code search for `sync_dir` and `dirsync` returns zero hits), and Erlang exposes no primitive for it, so a fix here would need a NIF. | [couch_file.erl L285–296](https://github.com/apache/couchdb/blob/b54baba6607b7605f036998db1877079c99e6f6d/src/couch/src/couch_file.erl#L285-L296) |
| **bbolt** (BoltDB, etcd's storage engine) | The Windows build's `fdatasync(db)` is `db.file.Sync()` — file-level. There is no directory operation anywhere in the repository: no `OpenDir`, no directory sync in `db.go`, `tx.go`, `bolt.go`, `freelist.go`, `bucket.go` or `page.go` | [bolt_windows.go L16–19](https://github.com/etcd-io/bbolt/blob/622eada16b5e733bc3bcacf17ca394452fc621f0/bolt_windows.go#L16-L19) |
| **FoundationDB** (Apple) | The rename API says it plainly: "Renames the given file. Does not fsync the directory." The Windows flush is file-level — `FlushFileBuffers` on the file handle inside `platform::atomicReplace`, next to `MoveFileExA(..., MOVEFILE_WRITE_THROUGH)` — and a scan of the whole tree finds no directory fsync at all | [Platform.h L369–370](https://github.com/apple/foundationdb/blob/bf64ce35c6b1ddec9acc281c8a1e5c62bfe4e0cb/flow/include/flow/Platform.h#L369-L370) · [Platform.cpp L2380–2385](https://github.com/apple/foundationdb/blob/bf64ce35c6b1ddec9acc281c8a1e5c62bfe4e0cb/flow/Platform.cpp#L2380-L2385) |
| **DuckDB** | The file-system interface exposes `FileSync(FileHandle&)` only — there is no `SyncDirectory`/`DirectorySync` anywhere, and the abstract base throws `NotImplementedException`. The Windows implementation is `FlushFileBuffers` on the file handle | [local_file_system.cpp L1675–1685](https://github.com/duckdb/duckdb/blob/b678afa97b529d5856731e115e31542a72d2d978/src/common/local_file_system.cpp#L1675-L1685) |
| **ScyllaDB** | A complete content scan of the repository (all 6,143 files) finds no `fsync(` call and no `_WIN32` code: the only fsync mentions are configuration knobs such as `trickle_fsync` and `commitlog_sync`, the file-level fsync lives in Seastar (an external submodule), and Windows is not supported | [db/config.cc L1077–1080](https://github.com/scylladb/scylladb/blob/382583448560bfaa9d3fcb57e1390310a620ce58/db/config.cc#L1077-L1080) |
| **CockroachDB** | Nothing directory-level in this repository — the WAL sync is Pebble's (row A). What is here syncs the log file: `syncBuffer.Sync()` is `sb.file.Sync()` | [file_sync_buffer.go L31–37](https://github.com/cockroachdb/cockroach/blob/d30c905fff79ef825adc96bcc647f1872a90f2ff/pkg/util/log/file_sync_buffer.go#L31-L37) |
| **ClickHouse** | The directory-sync guard is POSIX-only: `LocalDirectorySyncGuard` opens with `::open(full_path, O_DIRECTORY)` and calls `::fdatasync(sync_fd)`, with no `OS_WIN` branch anywhere — on Windows the open would fail and the guard throws `CANNOT_OPEN_FILE`/`CANNOT_FSYNC`. ClickHouse has no supported Windows build | [LocalDirectorySyncGuard.cpp L39–45](https://github.com/ClickHouse/ClickHouse/blob/58726cc5cfedf799138a8a1eac012f544007d4ae/src/Disks/LocalDirectorySyncGuard.cpp#L39-L45) · [L81–88](https://github.com/ClickHouse/ClickHouse/blob/58726cc5cfedf799138a8a1eac012f544007d4ae/src/Disks/LocalDirectorySyncGuard.cpp#L81-L88) |
| **Redis** | Redis has no Windows build at all, and its directory fsync is POSIX-only: `fsyncFileDir()` opens the directory with `open(dname, O_RDONLY)` and ignores `EISDIR`; there is no `_WIN32` branch in the file, so on Windows the open would fail with EACCES and the function returns −1 — an error, not a no-op | [util.c L1281–1293](https://github.com/redis/redis/blob/d92afbeb85aa02e245fe534946b7f69a6c0898c0/src/util.c#L1281-L1293) · [L1307–1315](https://github.com/redis/redis/blob/d92afbeb85aa02e245fe534946b7f69a6c0898c0/src/util.c#L1307-L1315) |

**C. The same claim in further projects (phrase sweep, 2026-11-15)**

A code search for the sentences themselves ("flush a directory on Windows", "does not support FlushFileBuffers on directory handles", "no need to sync directories") found the same claims outside the twelve above. Each is quoted verbatim at the commit it was read at; stars are as of the sweep. The reason fails for the same measured reason as group A.

| Project | What the code says | Where |
|---|---|---|
| **WiredTiger** (MongoDB's storage engine, 2,432★) | "We don't open Windows system handles when opening directories for flushing, as it's not necessary (or possible) to flush a directory on Windows." The sentence appears twice. | [source](https://github.com/wiredtiger/wiredtiger/blob/b71494dd9fa11e707adb68476d25e46257f38995/src/os_win/os_fs.c#L185-L187) |
| **Secblitz** (161★) | "Windows files use WRITE_THROUGH and FlushFileBuffers via sync_all; snapshot publication also uses MOVEFILE_WRITE_THROUGH. Windows does not support the Unix directory-fsync contract." | [source](https://github.com/secblitz/Secblitz/blob/4ab042f5a76c348e06a97bab6f39c12cdb6c295e/src/engine/fsio.rs#L147-L149) |
| **youta** (13★) | "Flushing NTFS metadata means FlushFileBuffers on a *volume* handle, which needs administrator rights and would flush every process's writes… So on Windows this is a documented no-op." | [source](https://github.com/vitaly-zdanevich/youta/blob/339417911b6bb047dcc51a93ae803874d8f8d796/src/durability.rs#L14-L17) |
| **libfreemkv** (15★) | "std cannot open a directory as a `File` on Windows (it does not set `FILE_FLAG_BACKUP_SEMANTICS`)… NTFS/ReFS commit a rename's directory entry without an explicit directory flush, so skipping it here is correct — not a durability regression." | [source](https://github.com/freemkv/libfreemkv/blob/4db1a72ee5ada587db48f3b3e6f15ee6285265eb/src/io/fsync/windows.rs#L1-L9) |
| **kibisis** (4★) | "std has no way to flush a directory on Windows, so there this does nothing." | [source](https://github.com/Perseus-Computing-LLC/kibisis/blob/74129f58ad45f4983649ae1d443e1e20ba915800/src/file_lease.rs#L303-L305) |
| **cicada** (2★) | "Windows does not support FlushFileBuffers on directory handles. File contents are flushed; directory durability depends on the host filesystem." | [source](https://github.com/odvcencio/cicada/blob/91acae1397d6b6fbbf9be639ec00b4dd5ecb0e87/host/takejournal/durable_windows.go#L17-L19) |
| **tinystack** (1★) | "Windows does not support FlushFileBuffers on directory handles (which requires GENERIC_WRITE and returns ERROR_ACCESS_DENIED or ERROR_INVALID_FUNCTION)." It names the requirement that makes the call succeed. | [source](https://github.com/amjadjibon/tinystack/blob/28319c7eac9d9597a9b446f36b6ead2cf697f128/internal/storage/archive/sync_windows.go#L10-L12) |

Five more repeat the sentence without adding a reason of their own:

- **Boxset** — "syncDir cannot flush a directory on Windows and never reports that it did." ([source](https://github.com/ViktorsBaikers/Boxset/blob/606296805c76ff0bba47618a4e5e4657544e07af/internal/install/syncdir_windows.go#L10-L11))
- **sobalink** — "Windows does not support FlushFileBuffers on directory handles." ([source](https://github.com/webkaz-labs/sobalink/blob/50e869360c4e505dcffe47cc891611a2ebb4a126/internal/config/atomic_temp_windows.go#L217-L219))
- **aiClean** — "Windows does not support FlushFileBuffers on directory handles." ([source](https://github.com/ttokunaga-ja/aiClean/blob/40c914096e1c06aa8720ea7d3714164bf68859f5/src/storage/core.rs#L1568-L1569))
- **sqlrs** — "Windows does not support FlushFileBuffers on directory handles." ([source](https://github.com/kimak-irmagi/sqlrs/blob/45e281ee987b5e591583b66cb64ec52b3d81dfc0/backend/local-engine-go/internal/prepare/liquibase_projection_windows.go#L18-L19))
- **browsermining** — "Windows does not support FlushFileBuffers on directory handles." ([source](https://github.com/CypherTroopers/browsermining/blob/9946c89ca55218320332ecb6048eb3fbc7b6379a/commonrpcreward/persist_windows.go#L18-L19))

**gateway** — "Directory fsync is not available through os.File on Windows" — true of a read-only `os.File`, false as a platform statement. ([source](https://github.com/Blockamoto/gateway/blob/31153b345d11e93c46a4cea7dd9d56394fe88a0a/durable_windows.go#L5-L7))


Not counted here: `calybris-core` (14★) says the standard library exposes no *portable* directory fsync, which is true of the portable API; `unmv` (0★) says "no generally supported equivalent" and explicitly disclaims crash consistency ("this is not a power-failure transaction"), so its decision stands even though a call exists.

**D. The hook exists on Windows, and it works**

| Project | What the code does | Where |
|---|---|---|
| **etcd** | A Windows-only helper opens a directory "with write access for syncing" — `CreateFileW` with `GENERIC_READ \| GENERIC_WRITE` and `FILE_FLAG_BACKUP_SEMANTICS` — and `fileutil.Fsync()` is called on that handle; on Windows it is `FlushFileBuffers` (Go's `syscall.Fsync` is `FlushFileBuffers`). Two call sites, both of them the parent-directory case: `fileutil.OpenDir(filepath.Dir(w.dir))` + `fileutil.Fsync(pdir)` when the WAL is created, and `fileutil.Fsync(w.dirFile)` after a WAL rename; the snapshot path does the same after `SaveDBFrom` renames the snapshot into place. The directory sync landed on 2026-08-25 in "fix: fsync snap directory when saving a received snapshot db"; the Windows helper is far older (the same file is already in v3.3.0, 2017). No administrator rights involved | [dir_windows.go L29–50](https://github.com/etcd-io/etcd/blob/1b969e26a2c65733f1ff696e3537373d56b3ae67/client/pkg/fileutil/dir_windows.go#L29-L50) · [wal.go L201–224](https://github.com/etcd-io/etcd/blob/1b969e26a2c65733f1ff696e3537373d56b3ae67/server/storage/wal/wal.go#L201-L224) · [wal.go L843–850](https://github.com/etcd-io/etcd/blob/1b969e26a2c65733f1ff696e3537373d56b3ae67/server/storage/wal/wal.go#L843-L850) · [snap/db.go L121–132](https://github.com/etcd-io/etcd/blob/1b969e26a2c65733f1ff696e3537373d56b3ae67/server/etcdserver/api/snap/db.go#L121-L132) · [Go: Fsync → FlushFileBuffers](https://github.com/golang/go/blob/go1.24.0/src/syscall/syscall_windows.go#L706-L707) · [commit cf31e1f](https://github.com/etcd-io/etcd/commit/cf31e1f6033f0752f0c55d2456a0771be0c5ba80) |

This is the same sequence this fork uses, in a database that ships it: same open (`GENERIC_READ | GENERIC_WRITE` + `FILE_FLAG_BACKUP_SEMANTICS`), same flush (`FlushFileBuffers`), same moment (the parent directory, after the file has been created or renamed into place). The only difference from the rows above is how etcd got there — it started from the POSIX requirement ("Linux fsync(2) documents that syncing a file does not necessarily persist its directory entry; the directory must also be synced") and let the platform helper decide how to do it, and the Windows helper happens to know that a handle must be writable to be flushed.

The likeliest explanation for the whole spread is not thirty-five independent engineering judgments but one documentation gap. MS-FSA §2.1.5.7 documents the behavior at the filesystem-implementer level (NTFS only, product note `<80>`, first draft 2009), and no Win32 reference page mentions directories at all: `FlushFileBuffers` describes files, and `CreateFile` documents `FILE_FLAG_BACKUP_SEMANTICS` for opening a directory — "A directory handle can be passed to some functions instead of a file handle", without saying which functions, or what a flushed directory handle persists. So the POSIX-shaped attempt — `open(dir, O_RDONLY)` then `fsync` — fails with `ERROR_ACCESS_DENIED`, which reads as "the platform does not support this", and the no-op gets written down with whatever rationale is nearest to hand. MySQL's `::open(parent_dir, O_RDONLY)` is that exact call.

Counted as a set: 35 projects — 3 silences, 1 deference, 2 "the OS already does it", 5 "the OS cannot" (or the platform is unsupported), 10 with no Windows directory operation at all, 13 that repeat the same sentences in the phrase sweep, and 1 that does it. The measurements contradict the categorical form of "the OS cannot" and settle nothing about the rest; what they leave untouched is why every one of those rows was written this way — nobody could find a document saying what an application is allowed to do.

Rows record the state before the 2026-11 pull requests. restic ([#22177](https://github.com/restic/restic/pull/22177)) is being changed by one; the same is proposed for leveldb [#1388](https://github.com/google/leveldb/pull/1388), Pebble [#6258](https://github.com/cockroachdb/pebble/pull/6258), RocksDB [#15682](https://github.com/meta/rocksdb/pull/15682), and SQLite ([bug-forum post](https://sqlite.org/bugs/forumpost/d8ebf597e52841a7e5f5c109bac75ee618680a95c0ecb83c4df6fe422c155bc9)). BadgerDB has no such proposal yet — its Windows no-op is the oldest of the set (2019). etcd needs no proposal: it has been doing it since 2026-08. kit, which is not a database and therefore not a row here, also has an open PR ([#345](https://github.com/speakeasy-api/kit/pull/345)) implementing the same sequence. MySQL has a patch on [my fork branch](https://github.com/takomitkm/mysql-server/tree/fix/windows-dirflush) that has not been proposed upstream yet; LMDB and CouchDB have patches or analysis in progress but nothing public.
