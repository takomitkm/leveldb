# E34 — Status: rig delivered, crash trials blocked on the QEMU host

**Date:** 2026-11-24
**Branch:** `e34-ntfs-index-crash-replay`
**Plan:** [E34-PLAN.md](E34-PLAN.md)
**CI run:** [45323673899](https://github.com/takomitkm/leveldb/actions/runs/45323673899) (success)

## What this file is

`E34-PLAN.md` pre-registers the E34 experiment: after a candidate flush
returns successfully and the machine loses power immediately, does NTFS log
replay restore the confirmed directory-index change?

This file records the **state of the work**, not results. **No crash trial has
been run yet**, so there is no recovery evidence and no R0–RX classification to
report. Nothing here may be read as a durability result.

## Delivered

| Piece | File | State |
|---|---|---|
| Pre-registered design | `E34-PLAN.md` | done |
| Guest probe (namespace change + candidate flush + external channel) | `e34/e34-probe.cpp` | builds with MSVC; runs |
| Host controller (manifest, clock alignment, crash trigger) | `e34/e34-crashctl.py` | runs; exercised live in CI |
| Pre-recovery forensic decode | `e34/e34-preimage.py` | runs; validated on the E33.1 `$LogFile` images |
| Recovery check + classification | `e34/e34-recover.py` | runs; unit-tested |
| Statistics (per arm, 3/N bound) | `e34/e34-aggregate.py` | runs; unit-tested |
| Single-trial QEMU driver | `e34/e34-trial.sh` | written, **not yet executed** |
| Self-tests for the analysis chain | `e34/e34-selftest.py` | 30 checks, all green in CI |
| CI validation | `.github/workflows/e34-selftest.yml` | green |

## What CI actually validated (run 45323673899)

* the probe compiles with MSVC (182,272 bytes);
* the analysis self-tests pass (controller manifest reduction, clock
  alignment, the four pre-recovery states, every R0–RX branch, the statistical
  bound);
* a **live external-channel trial**: the probe created a file on a real NTFS
  volume, ran the arm-C directory flush, and the controller recorded all
  eight events — `trial_start, dir_info, op_start, op_end, flush_start,
  flush_end, trial_end, rehearsal_done` — with the guest-to-host clock offset
  measured (−10894 × 100 ns) and the channel overhead at ~0.2 ms median;
* the E33 decoders still pass their self-tests, so the pre-recovery decode
  path is usable against real `$LogFile` images.

## Bootstrap crash trial (rig validation, not a result)

The rig host became reachable, so a bootstrap trial was run through the
**existing E17 probe** (`e17-run.sh 1 3`, arm 1 create + parent-directory
flush = E34 arm C create) purely to validate the crash chain. It is excluded
from E34's R0-RX ledger because its manifest was written to the tested volume
(E34-PLAN.md section 8 forbids that). Full record:
[E34-BOOTSTRAP.md](E34-BOOTSTRAP.md).

What it established on real artifacts:

* DONE marker at 40 s, QEMU force-killed without a graceful shutdown, and the
  crashed volume plus the blklogwrites journal preserved;
* `e34-preimage.py` found the `$LogFile` on the crashed 4 GiB image, decoded
  36 index operations, attributed two of three test files with LSN + RCRD
  page (a1_r0.bin at LSN 8396124 page 14; a1_r1.bin at LSN 8396762 page 15),
  and correctly labelled the third `overwritten`;
* `e34-namespace.py` (pytsk3, no replay) listed all three created files in
  the raw namespace.

No R0-RX classification and no durability claim is made from this trial.

## What is still blocked for the real matrix

The crash trials need a host that can **cut the power** while keeping the
volumes byte-identical afterwards:

* a QEMU host with the E14/E17/E29–E31 rig (blklogwrites journal, Windows
  guest image);
* the ability to take a pre-recovery copy of the crashed disk image before
  anything mounts it.

A GitHub Actions runner cannot provide either. The runner is itself a VM
whose storage cache model is not documented, so "kill the workload" is a guest
crash, not a power cut, and there is no way to snapshot the runner's disk
before the OS replays anything. E34-PLAN.md section 3.1 requires this to be
*proved* before a trial may be called a power-cut trial, and section 11
requires an invalid trial to be classified RX rather than quietly counted.

The remaining hard step is injecting `e34-probe.cpp` into the WinPE image:
the probe is `/e13pe.exe` inside the WIM (both image indexes), and the rig
host has no wimlib and no elevated DISM. The rig's SSH tunnel is now working
(via `school-guest-pc2`), so the host is reachable.

**Consequence:** E34 is *instrumented and its harness chain is validated by a
bootstrap trial*, but the structured matrix has not been run. No arm may be
reported as L4 or L5 on the strength of E33.1 or the bootstrap trial.

## Bugs the CI cycle found (all fixed)

The CI job was worth running even without a crash: it found five real defects
that a local-only build would have hidden.

1. **The probe did not compile.** The Windows SDK spells the
   `BY_HANDLE_FILE_INFORMATION` members `nFileIndexHigh` / `nFileIndexLow` /
   `dwVolumeSerialNumber`; the .NET-style names only existed in the E33
   PowerShell probe's own struct declaration.
2. **The readiness probe stole the guest's connection.** The CI step checked
   that the controller was listening with `Test-NetConnection`, which opens a
   TCP connection. The controller accepts one connection, so the probe
   consumed the guest's slot and the real probe was rejected with
   "cannot reach the controller".
3. **The event line was not valid JSON.** The target directory contains
   backslashes (`D:\e34-rehearsal`); the probe now escapes backslashes,
   quotes and control characters in every environment-derived string.
4. **The rehearsal stopped too early.** The controller's default crash point
   is `flush_end`, so it cut the connection before the probe emitted
   `trial_end`; the rehearsal now uses `rehearsal_done` (a real crash still
   uses `flush_end`).
5. **The kill interval mixed two clocks.** It subtracted a wall-clock value
   from a FILETIME; both are now FILETIME.

A sixth defect was caught by the self-tests before CI saw it: the failure
bound was computed as `1 − 0.95^(1/N)` instead of `1 − 0.05^(1/N)`, which
would have overstated the confidence of a zero-failure arm by two orders of
magnitude (0.17% instead of 9.5% at N = 30).

## The four-state vocabulary, exercised on real data

`e34-preimage.py` was run against the E33.1 arm-F `$LogFile` image (64 MiB,
10,899 decoded index operations, 3,165 parsed pages). For the two expected
names of that arm it reported:

| expected name | state | why |
|---|---|---|
| `e33_F_del_tgt.bin` | positively identified | found at LSN 35129477, RCRD page 3076, transaction 224 |
| `e33_F_create_0000.bin` | overwritten | the circular log no longer carries it |

This is the vocabulary doing its job: the second file is *not* proven absent,
only proven to be gone from the snapshot, and the report says so.

## Next step

Bring up the QEMU rig host, then run the trial matrix from
`e34-trial.sh`: arms A/B/C/F/I/K × create/rename/delete × large/small
topology × the five crash points, 30+ valid trials per primary arm per
operation, with the pre-recovery copy taken before any mount. The results
belong in `E34-RESULTS.md`, which does not exist yet.

Until then, the answer to E34's question is unchanged from E33.1: **not
measured**.
