Aggregate #4 of the personal [google/leveldb](https://github.com/google/leveldb)
patch fork. **Not an official LevelDB release.**

## Choosing a Windows package

Every Windows archive is available with an `-acp` or `-utf8` suffix:

- **ACP:** for existing applications passing filenames in the Windows file API
  code page, such as GBK on a Chinese system. This restores upstream's narrow
  path interpretation while retaining the data-safety fixes.
- **UTF-8:** for applications that explicitly pass UTF-8 filenames. The fork's
  CMake source-build default remains UTF-8.

Keys, values and valid database contents do not need conversion between these
path variants. Neither variant promises unconditional binary compatibility
across compilers, architectures, CRT settings or optional codec configurations.
See the [compatibility guide](https://github.com/takomitkm/leveldb/blob/agg.4/docs/COMPATIBILITY.md).

## Changes since agg.3

- Added the Windows path-mode build option and publish both variants. File
  access, logger creation, directory enumeration and test-directory paths use
  the selected interpretation consistently. The former UTF-8 build still used
  narrow-code-page operations for logger and temporary-directory paths.
- Fixed log recovery with custom `SequentialFile` implementations that return
  their own reusable buffers. Both full-block and short-read regressions compare
  complete records, including binary and multi-block records.
- Fixed the Zstd symbol probe (`ZSTD_compress`) and generated configuration-header
  guard. Codec-enabled builds can now exercise the intended compression path.
- Scoped exception/RTTI settings to LevelDB instead of rewriting a consuming
  project's flags. Both follow compiler defaults unless explicitly disabled.
- Added recovery terminator tests, Windows file-operation regressions, fuzz seed
  replay, and Debug/Release checks for optional dependencies. GCC also executes
  the Kyoto Cabinet benchmark instead of only compiling it.
- Replaced manual binary assembly with GitHub Actions builds. Packages contain
  the full public headers, libraries, relocatable CMake package, licenses and an
  exact-source build record. This fixes the incomplete header directory in the
  older Windows ZIPs.

## Binary configuration

All release binaries explicitly disable Snappy, Zstd, external CRC32C and
tcmalloc. Build from source with the required codecs to read compressed blocks
written using those codecs. The built-in checksum implementation remains active.

MSVC static packages use `/MT` for Release and `/MTd` for Debug; static-library
consumers must match the CRT and iterator-debugging configuration. MSVC DLL
packages use `/MD` for Release and `/MDd` for Debug, with consumers using the same
CRT so C++ heap allocations can cross the DLL boundary safely. Release DLLs
require the matching Microsoft Visual C++ Redistributable. Debug DLLs require
the matching Visual Studio debug CRT, which is not included in the ordinary
redistributable runtime. Debug and Release libraries are not interchangeable.
Source builds leave CRT selection to the calling CMake project or toolchain;
they do not force the release-package CRT policy.

MinGW and Zig packages require their matching compiler ABI. Inspect
`BUILD-INFO.json` for each archive's exact source, toolchain and settings.

Both Windows path variants retain the existing durable rename and MANIFEST
parent-directory flush. Directory-open/flush errors are propagated; unsupported
filesystems, permissions or network shares require separate deployment testing.
Corruption checks are stricter, but there is no new valid-data format migration.

## Build and package verification

The release workflow builds and tests each published configuration, installs it,
archives it, extracts the archive, then compiles and runs an independent public
API consumer. Windows checks include the selected non-ASCII path encoding and
log file. A failed build, test or package check blocks publication.

Each binary archive includes `BUILD-INFO.json`; `SHA256SUMS` covers the uploaded
binary archives. The workflow logs are linked from the release build record.

[Full source comparison](https://github.com/takomitkm/leveldb/compare/agg.3...agg.4)
