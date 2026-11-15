#!/bin/bash
# E14 v3 autopsy (guest): mount crash.img twice —
#   1) ro,norecover  : raw on-disk state (no journal replay)
#   2) rw            : ntfs-3g replays the journal like Windows would
# Lists /e14 after each. The rw pass modifies the local copy only.
IMG=/home/ci/e13/crash.img
sudo mkdir -p /mnt/e14a
sudo losetup -P -f --show "$IMG" > /tmp/loop.txt
L=$(cat /tmp/loop.txt)
echo "== RO-NORECOVER =="
sudo mount -t ntfs-3g -o ro,norecover "${L}p1" /mnt/e14a 2>&1 | head -3
ls /mnt/e14a/e14/ 2>&1
sudo umount /mnt/e14a 2>/dev/null
echo "== RW-REPLAY =="
sudo mount -t ntfs-3g "${L}p1" /mnt/e14a 2>&1 | head -3
ls /mnt/e14a/e14/ 2>&1
sudo umount /mnt/e14a 2>/dev/null
sudo losetup -d "$L" 2>/dev/null
echo AUTOPSY3-DONE