# E34 — Bootstrap crash trial (rig validation, not a classification result)

**Date:** 2026-11-24
**Rig host:** the Windows/MSYS2 QEMU host reached through `school-guest-pc2`
**Rig:** QEMU 11.1.2, TCG, data disk behind `blklogwrites`
**Trial:** the existing E17 arm 1 (create + `FlushFileBuffers(parent directory)`),
3 reps, run through `e17-run.sh` — i.e. the **E34 arm C create** shape

## Why this is a bootstrap, not an E34 result

This trial used the **existing** `pe-e17.iso`, whose probe writes its manifest
to the *tested volume* (`\e14\go.txt`, `\e14\arm.txt`). E34-PLAN.md §8 requires
the manifest to be recorded over an **external** channel, because a manifest
on the volume under test cannot be trusted as pre-crash evidence.

The E34 probe (`e34/e34-probe.cpp`) has been updated and builds, but injecting
it into the WinPE image needs WIM editing (wimlib) and ISO rebuilding, neither
of which is available on the rig host. So this bootstrap trial validates the
**harness chain** — power cut, image preservation, pre-recovery `$LogFile`
decode, offline namespace check — and is **excluded from E34's R0–RX ledger**.

## What actually ran

```
cd /d/yzd/leveldb-e2e-yhPf93BW
bash e17-run.sh 1 3
```

Result from `e17-run-a1.log`:

```
DONE-MARKER at 40s
== force kill (never graceful: a clean quit would flush the guest cache) ==
log-records-visible=4
log-size=4294967296
== kept: e17-base-a1.img e17-vm-a1.img e17-log-a1.img ==
```

The guest was force-killed while the blklogwrites journal recorded every
write and flush. No graceful shutdown was issued.

Artifacts kept:

- `e17-vm-a1.img` — 4 GiB, the crashed data volume (downloaded locally for
  analysis);
- `e17-base-a1.img` — the pristine baseline;
- `e17-log-a1.img` — the blklogwrites journal;
- `e17-crash-a1-worst.img` / `e17-crash-a1-best.img` — offline replay images
  produced by the E17 harness (worst = everything after the last flush lost).

## Pre-recovery `$LogFile` decode (E34 §9)

The crashed image is a 1 MiB-offset MBR with a single NTFS partition,
512 B sectors / 8 sectors per cluster (4096 B clusters), `$MFT` at LCN 4.
Its `$LogFile` restart page was located at volume byte offset
2,148,683,264 (2048 MiB + 512 KiB); the first 64 MiB were extracted and
decoded with `e34-preimage.py`:

```
e34-preimage: 36 index records, states={"positively identified": 2, "overwritten": 1}
log bytes: 67108864   parsed pages: 17   undecodable: 0
  a1_r0.bin -> positively identified   LSN 8396124   RCRD page 14
  a1_r1.bin -> positively identified   LSN 8396762   RCRD page 15
  a1_r2.bin -> overwritten             (circular log no longer carries it)
```

The four-state vocabulary worked as designed: a record absent from the
circular log is reported as *overwritten*, not as "never written".

## Raw namespace check (E34 §9, before replay)

`e34-namespace.py` (pytsk3, read-only, no journal replay) against the crashed
image at volume offset 1 MiB:

```
e14/a1_r0.bin
e14/a1_r1.bin
e14/a1_r2.bin
e14/arm.txt
e14/done.txt
e14/go.txt
```

All three created test files survived in the **home pages** already, so this
particular trial does not depend on log replay to explain its namespace.

## What this validates

1. The QEMU host is reachable again and the blklogwrites crash chain runs
   end-to-end (DONE marker, force kill, kept images).
2. The E34 pre-recovery toolchain works on a real crashed image: it found the
   `$LogFile`, decoded 36 index operations, attributed two of three test files
   with LSN + RCRD page, and correctly labelled the third "overwritten".
3. The E34 namespace tool enumerates the raw (no-replay) namespace, so the
   pre/post comparison required by §9–§10 is implementable.

## What this does NOT validate

* **No external manifest** — the manifest was on the tested volume (E17's
  design), so this trial is excluded from E34's ledger (RX by §11).
* **No NTFS replay observation** — pytsk3 enumerated the raw platter state;
  no Windows/WinPE mount was run on the post-crash copy, so no replay result
  is recorded here.
* **No R0–RX classification** — no classification is claimed.
* **No handle/API comparison** — this was a single arm C create shape, not a
  matrix across arms/operations/topologies/crash points.
* **No statistics** — one trial says nothing about durability.

## Remaining blockers for the real matrix

1. Inject `e34-probe.cpp` into a WinPE ISO: replace the WIM's `/e13pe.exe` in
   both image indexes (needs wimlib or elevated DISM on the rig host), then
   rebuild the ISO (`xorriso` is available on the rig host).
2. Run the controller in the guest's place: since WinPE has no network and
   the probe cannot use a TCP controller there, use its COM1/manifest
   channels; the host watches the blklogwrites journal for `E34DONE` and cuts
   the power at the configured delay.
3. For the post-recovery check, boot the post-crash copy in Windows/WinPE and
   enumerate the target directory (E34-PLAN.md §10) rather than relying on
   pytsk3's no-replay listing.
