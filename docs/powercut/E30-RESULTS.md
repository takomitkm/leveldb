# E30 — The exact NT-level `FILE_WRITE_THROUGH` shapes under the power-cut journal (2026-11-21)

**Question** (closes the gap recorded in E29: "the exact NT-level shapes
(E27-F/G) have ETW-level evidence only"): when the handle that performs the
namespace change itself is `FILE_WRITE_THROUGH` — `NtCreateFile` with
`FILE_CREATE | FILE_NON_DIRECTORY_FILE | FILE_WRITE_THROUGH`, or
`NtSetInformationFile(FileRenameInformation)` / `FileDispositionInformation`
on a handle opened with `FILE_WRITE_THROUGH` — does the namespace change reach
the device with a barrier at operation return?

**Answer: no.** At the journal layer the name bytes appear only after the last
barrier (or only through writeback timing), and the offline autopsy finds no
directory entry on the platter at either cut. This matches E29's Win32
equivalents (`MoveFileExW(MOVEFILE_WRITE_THROUGH)`, plain `CreateDirectoryA` /
`DeleteFileW`) and the E27/E28 ETW floor reading, and completes the chain: the
operation-level write-through idea fails at every layer tested.

## Rig

Same E17 rig (QEMU/TCG + WinPE 19041, data disk under `blklogwrites`, arm id
via SMBIOS serial, one arm per boot, guest killed right after the DONE marker;
journal replayed offline at worst/best cuts; ntfs-3g `ro,norecover` autopsy).
Probe: `e30pe.cpp` (= `e17pe.cpp` + NT create/rename/delete helpers; arms
34–37), built with Debian mingw `x86_64-w64-mingw32-g++ -O2 -static`.
ISO: `pe-e30b.iso`, 750,540,800 bytes, sha256 `66d946e1e2a2f12e6e245402…`.

## Arms and results (4 arms × 3 reps; all `errs=0`, all `how=S`)

| arm | sequence | rep0 name_first / barrier | rep1 | rep2 |
|---|---|---|---|---|
| 34 | `NtCreateFile(FILE_CREATE, FILE_NON_DIRECTORY_FILE\|FILE_WRITE_THROUGH)` + write + close | 120 / 110 | 134 / 110 | 147 / 110 |
| 35 | pre-create src → open with `FILE_WRITE_THROUGH` → `NtSetInformationFile(FileRenameInformation)` | src 120, dst 121 / 110 | 131 / 110 | 141/142 / 110 |
| 36 | pre-create src → open with `FILE_WRITE_THROUGH` → `SetFileInformationByHandle(FileDispositionInformation)` | 120 / 110 | 130 / 110 | 139 / 110 |
| 37 | create tmp with `FILE_WRITE_THROUGH` + write + `FlushFileBuffers(tmp)` → rename via FWT handle | tmp 120 **< 126**, final 127 / 126 | tmp 137 < 142, final 143 / 142 | tmp 153 < 159, final 160 / 159 |

`name_first` = first journal record index containing the trial name as
UTF-16LE (`$FILE_NAME` / `$I30` bytes); `barrier` = last FLUSH/FUA barrier
before the next marker. A name at/after the barrier reached the device only
through writeback timing, not through the operation's contract.

- Arms 34–36: every name first appears **after** the last barrier — the
  operation itself issues no flush.
- Arm 37: only the temp file's own explicit `FlushFileBuffers` is a barrier;
  the publication rename's target (`final_*`) always appears **after** it.

## Autopsy (ground truth)

8 crash images (4 arms × worst/best), mounted `ro,norecover`:

**every image contains only `/e14/arm.txt` — no `e30_*`, `src_*`, `dst_*`,
`tmp_*` or `final_*` entries.**

The create/rename/delete namespace changes are not visible on the platter at
the worst cut *and* not even at the best cut. Arm 37's flushed temp file shows
the familiar E14-arm-5 shape: the record/metadata may reach the device, but
the parent directory entry does not, so nothing is listable.

## Rig findings worth recording

- **Build bug 1 (stale ISO):** `genisoimage` was fed `e30tree/` (a leftover
  from E31's build) while the WIM updates applied to `e30btree/`. The ISO
  therefore carried E31's wrapper probe — which understands `E29ARM`/
  `E17ARM` only and parks on an unknown `E30ARM` id. Symptom: every arm boots,
  idles, zero `E17DONE`, zero writes. Fixed by pointing `genisoimage` at the
  tree that was actually updated; the rebuilt ISO is 14 MB smaller (no
  rightkit PEs) and works.
- **Build bug 2:** arms 35/36 opened a source file that was never created
  (the pre-state was missing); fixed by pre-creating `src_N` before the
  operative open.
- Diagnostic recipe that localized bug 1: scan the blklogwrites log for the
  marker strings with a control run on a known-good log (E31's log finds
  `E29GO`/`go.txt`), then `screendump` the live VM and check whether the
  console window exists. A probe that parks before writing anything leaves
  the screen at the WinPE desktop with an idle console and zero journal
  records.

## Reconciliation

- E27/E28 (ETW): rename/delete shapes at the flush floor, no metadata burst;
  E30 confirms the same shapes at the power-cut level.
- E29: the Win32 equivalents (`MOVEFILE_WRITE_THROUGH`, `CreateDirectoryA`,
  `DeleteFileW`) show zero reproducible barriers; E30 shows the NT-level
  forms behave identically.
- The only mechanism in the family that pushes namespace metadata to the
  device *with* a barrier remains `FlushFileBuffers` on the volume root
  (E17 arms 11/12/14/15), withdrawn for privilege reasons (E26, 2026-11-21).

## Artifacts

In this repository:  (probe source, build/run/launch/autopsy scripts and the verdict driver, mirrored from the rig).
- School machine `D:\yzd\leveldb-e2e-yhPf93BW\`: `e30-{base,vm,log}-a{34..37}.img`,
  `e30-crash-a*-{worst,best}.img`, `e30-autopsy.log`, `e30_verdict.py`.
- Probe/scripts: `e30pe.cpp` (guest `/home/ci/e13/`), `e30-build.sh`,
  `e30-run.sh`, `e30-launch.ps1`, `e30-all.sh`, `e30-autopsy.sh`.

Scope: WinPE 19041, QEMU IDE/TCG; Win11 `ntfs.sys`, NVMe/virtio, ReFS, SMB and
a physical power cut (Tier-2) remain untested.