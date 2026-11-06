# Compatibility with upstream LevelDB

The public C and C++ function signatures and class interfaces match the upstream
baseline `bb74ef7`. A matching interface alone does not make every prebuilt
binary interchangeable. Choose the path encoding, compiler, architecture,
runtime and optional codecs required by the application.

## Windows path packages

Every Windows binary is published in two variants:

| Suffix | Filename and directory-name encoding | Intended use |
|---|---|---|
| `-acp` | Original narrow Win32 file API code page (normally the system ANSI code page; the OEM file API setting is honored) | Existing applications that pass native-code-page bytes, such as GBK on a Chinese Windows installation |
| `-utf8` | UTF-8 | Applications that explicitly convert paths to UTF-8 before calling LevelDB |

Both variants retain the durability, short-read and corruption fixes. They do
not change database keys, values or the on-disk record format. Copying a valid
existing database does not require converting its contents; pass the directory
name in the encoding expected by the selected library.

`Env::Default()` applies the selected encoding consistently to file access,
`GetChildren()` results, logger paths and test-directory paths. A custom `Env`
continues to define its own path handling. As with the original narrow Win32
APIs, the ACP variant cannot represent characters outside its active code page.

For source builds, use:

```sh
cmake -S . -B build -DLEVELDB_WINDOWS_UTF8_PATHS=OFF # ACP compatibility
cmake -S . -B build -DLEVELDB_WINDOWS_UTF8_PATHS=ON  # UTF-8 (fork default)
```

The option is private to the library and does not change the public headers.
Linux paths remain byte strings, with no new encoding conversion.

## Compiler and runtime compatibility

C++ libraries must match the application's architecture and compiler ABI. An
MSVC `.lib` and a MinGW/Zig `.a` are different toolchain products.

MSVC static packages use `/MT` for Release and `/MTd` for Debug. Static-library
consumers must use the same CRT and iterator-debugging configuration. Applications
using `/MD` or `/MDd` must build a static LevelDB from source with that runtime.

MSVC DLL packages use `/MD` for Release and `/MDd` for Debug; consumers must match
these settings so C++ objects and heap allocations crossing the DLL boundary use
the same CRT. Release DLLs require the matching Microsoft Visual C++
Redistributable. Debug DLLs require the matching Visual Studio debug CRT, which
is not part of the ordinary redistributable runtime. Debug and Release libraries
are not interchangeable.

These CRT settings apply to the release packages. Source builds leave the CRT
selection to the calling CMake project or toolchain and do not force `/MT` or
`/MD`.

The fork's source build defaults leave exceptions and RTTI enabled. Upstream's
CMake build forcibly disabled them. The `LEVELDB_DISABLE_EXCEPTIONS` and
`LEVELDB_DISABLE_RTTI` options remain available for coordinated source builds;
settings are scoped to the library rather than rewriting the consuming
project's global flags. Turning compiler exception support on does not add a
promise that arbitrary allocation failures can be recovered from safely.

Each archive's `BUILD-INFO.json` identifies its exact commit, toolchain,
configuration, path mode and dependency settings. System C/C++ runtime and
threading requirements still apply to static libraries.

## Optional compression and checksum libraries

Release binaries explicitly disable Snappy, Zstd, external CRC32C and tcmalloc,
so installed packages on a runner cannot silently change the artifact's
requirements. This does not disable LevelDB's built-in checksum implementation.

Applications that need to read Snappy- or Zstd-compressed data blocks must build
with the matching codec available. Codec-disabled binaries cannot read those
blocks. A source build that enables Zstd and explicitly selects
`kZstdCompression` now writes real compressed blocks because the Zstd symbol
probe is fixed. Older builds without a working decoder cannot read them.
The default compression selection and compression type numbers are unchanged.

The separate `optional-deps` CI job tests all four optional libraries in Debug
and Release. Its output is a test result, not the source of the release assets.

## Behavioural changes retained in both path variants

- Windows MANIFEST synchronization now opens and flushes the parent directory
  before flushing the file, and propagates failures. A filesystem or permission
  setup that cannot perform these operations may report an I/O error where the
  older implementation returned success. Other filesystems and network shares
  need deployment-specific verification.
- Windows rename uses `MoveFileExW` with replacement and write-through flags.
  The tests cover replacement and subsequent visibility; they do not simulate
  a physical power failure.
- Corrupt internal keys, overflowing entries and out-of-range block handles are
  rejected earlier. Incomplete WAL/MANIFEST tails are not reused for appending.
  These checks do not introduce a new format for valid databases.
- `CURRENT` also accepts CRLF and CR terminators. Writers keep their usual
  format.
- Directly reopening the same Windows logger appends instead of truncating.
  Normal database opening still attempts to rotate `LOG` to `LOG.old`.
- The Windows clock uses the precise system-time API when available, with a
  runtime fallback. This is not a promise that the clock is monotonic.
- Log reads honor custom `SequentialFile` implementations that return their
  own reusable buffers, in addition to implementations that use the supplied
  scratch memory. Regression tests cover full reads and repeated short reads.

## Package verification

The release workflow installs into a staging prefix, archives that prefix,
extracts the resulting archive and builds an independent CMake consumer against
it. Tests check the complete public header directory and installed CMake package,
then create, close and reopen a database. Windows checks include non-ASCII paths
and the actual log file. This also guards against the incomplete Windows header
directory in the older hand-built `agg.3` ZIP packages.
