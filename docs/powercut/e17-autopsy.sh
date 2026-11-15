#!/bin/bash
# E17 autopsy (Debian CI guest): mount crash image twice —
#   1) ro,norecover : raw on-disk state (no journal replay)
#   2) rw           : ntfs-3g replays the journal like Windows would
# Then grep the mounted FS for each arm's unique 2048B body strings
# ("e17-a<arm>-r<rep>") via the file NAMES (content-addressing is done
# host-side by e17_analyze.py against raw bytes; here names suffice).
# Derived from e14v3-autopsy.sh; takes the crash image path as $1.
IMG="${1:?crash img}"
sudo mkdir -p /mnt/e17a
sudo losetup -P -f --show "$IMG" > /tmp/loop17.txt
L=$(cat /tmp/loop17.txt)
echo "== RO-NORECOVER =="
sudo mount -t ntfs-3g -o ro,norecover "${L}p1" /mnt/e17a 2>&1 | head -3
ls /mnt/e17a/e14/ 2>&1 | head -40
sudo umount /mnt/e17a 2>/dev/null
echo "== RW-REPLAY =="
sudo mount -t ntfs-3g "${L}p1" /mnt/e17a 2>&1 | head -3
ls /mnt/e17a/e14/ 2>&1 | head -40
sudo umount /mnt/e17a 2>/dev/null
sudo losetup -d "$L" 2>/dev/null
echo E17-AUTOPSY-DONE
