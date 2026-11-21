# E31 — rightkit's public-API single operations under the power-cut journal (2026-11-21)

**Question** (closes the second gap recorded in E29: "the E29 ISO carried the
rightkit single-op binaries, but the wrapper arms did not launch them"): the
consumer-facing rightkit-fs API (`StateDir::create`, `rename_exclusive`,
`remove`, atomic `write`) claims operation-level `FILE_WRITE_THROUGH`
("`FILE_WRITE_THROUGH` on the handle that creates a directory, renames or
deletes … so they commit with the call. A power-loss test has not been run
against that claim."). Do those public operations leave the namespace change
on the device at operation return?

**Answer: no.** The journal shows the target names at/after the last barrier
(or, for the atomic write, only the internal temp file's flush is a barrier),
and the offline autopsy finds no `rk_*` entry on the platter in any arm at
either cut.

## Rig

E17 rig (QEMU/TCG + WinPE 19041, `blklogwrites`, SMBIOS arm id, one arm per
boot, force kill after the DONE marker; offline worst/best replay; ntfs-3g
`ro,norecover` autopsy). Probe: `e29pe.cpp` wrapper compiled to
`e30-e29pe.exe` (Debian mingw, static); arms 20–23. The wrapper launches the
**unmodified** rightkit single-op binaries (rev `8ab2b1051f40`, static build)
with the trial path `X:\e14\rk_<rep>`:

| arm | binary | operation |
|---|---|---|
| 20 | `rightkit-create-dir.exe` | `StateDir::create(X:\e14\rk_N)` |
| 21 | `rightkit-rename.exe` | `rename_exclusive(parent/src → X:\e14\rk_N)` |
| 22 | `rightkit-delete.exe` | create `rk_N`, write, `state.remove(rk_N)` |
| 23 | `rightkit-write.exe` | `state.write(rk_N, [0x57; 2048])` (atomic write) |

ISO: `pe-e30.iso`, 764,964,864 bytes, sha256 `1d136b272534580b42a13343…`
(built with the E29-v8 minimal-delta rule: tree17 boot files byte-identical).

## Arms and results (4 arms × 3 reps; all `errs=0`, all `how=S`)

| arm | rep0 name_first / barrier | rep1 | rep2 |
|---|---|---|---|
| 20 create-dir | 133 / 114 | 143 / 114 | 152 / 156 |
| 21 rename | 121 / 110 | 129 / 110 | 138 / 110 |
| 22 delete | 121 / 110 | 130 / 110 | 139 / 110 |
| 23 atomic write | 122 / 123 | 136 / 137 | 150 / 152 |

- Rename and delete: every target name first reaches the device **after** the
  last barrier — no barrier from the operation.
- Create-dir: 2 of 3 reps after the barrier, 1 before (weak/inconsistent) —
  the same instability E27/E28 saw for the create analog.
- Atomic write: the target name appears just **before** the barrier in all
  three reps — but that barrier is the atomic-write implementation's own
  temp-file `FlushFileBuffers` stage (the crate's `write()` is
  create-temp → write → flush-temp → publish-rename). The publication rename
  itself contributes nothing (E29 measured the same composite shape with
  `MOVEFILE_WRITE_THROUGH`).

## Autopsy (ground truth)

8 crash images (4 arms × worst/best), mounted `ro,norecover`:

**every image contains only `/e14/arm.txt` — no `rk_*` entry of any kind.**

Consequences:
- `StateDir::create` does not leave the new directory on the platter.
- `rename_exclusive` does not leave the destination entry.
- `remove` does not persist a deletion (there is no entry to delete).
- The atomic `write` leaves no published file: the temp-file flush may put
  its record on the device, but the dirent is absent, which is the
  E14-arm-5 / E29-arm-33 "durable data, non-durable namespace" shape.

The rightkit-fs README's own caveat ("A power-loss test has not been run
against that claim") now has its result: **the claim does not hold under a
power cut.**

## Reconciliation

- E27/E28 ETW: rename/delete at the flush floor, create unstable → E31 shows
  the same ranking at the power-cut layer.
- E29: the Win32 equivalents of the same shapes measure negative → E31
  measures the actual crate negative.
- E30 (same date): the minimal NT extraction of the same flag measures
  negative → both the crate and the flag are covered.
- Standing positive control: volume-root `FlushFileBuffers` (E17 arms
  11/12/14/15; withdrawn for privilege reasons per E26, 2026-11-21).

## Artifacts

- School machine `D:\yzd\leveldb-e2e-yhPf93BW\`: `e31-{base,vm,log}-a{20..23}.img`,
  `e31-crash-a*-{worst,best}.img`, `e31-autopsy.log`, `e31_verdict.py`.
- Probe/scripts: `e29pe.cpp`, `e31-build.sh`, `e31-run.sh`, `e31-launch.ps1`,
  `e31-all.sh`, `e31-autopsy.sh`.

Scope: WinPE 19041, QEMU IDE/TCG, static mingw-built rightkit binaries
(rev 8ab2b1051f40); Win11 `ntfs.sys`, NVMe/virtio, ReFS, SMB and a physical
power cut (Tier-2) remain untested.