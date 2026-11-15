#!/usr/bin/env bash
# E14 v3 master data disk (runs INSIDE Debian CI guest): 4G raw, NTFS E13SYS,
# /e14 dir + fallback arm.txt=98. Output: base14.img.gz for the host.
set -uo pipefail
cd /home/ci/e13 || exit 2
rm -f base14.img base14.img.gz
truncate -s 4G base14.img
parted -s base14.img mklabel msdos
parted -s base14.img mkpart primary ntfs 1MiB 100%
parted -s base14.img set 1 boot on
LOOP=$(sudo losetup -f --show --offset 1048576 base14.img)
echo "loop=$LOOP"
sudo mkntfs -F -q -L E13SYS "$LOOP"
sudo mkdir -p /mnt/e14m
sudo mount -t ntfs-3g "$LOOP" /mnt/e14m
sudo mkdir -p /mnt/e14m/e14
printf 'arm=98 reps=3' | sudo tee /mnt/e14m/e14/arm.txt >/dev/null
sudo sync
sudo umount /mnt/e14m
sudo losetup -d "$LOOP"
gzip -1c base14.img > base14.img.gz
echo "base14.gz=$(stat -c %s base14.img.gz)"
echo V3-MKBDATA-DONE