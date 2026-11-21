#!/usr/bin/env bash
# E31 autopsy (school host): stream each crash image to the Debian CI guest with
# on-the-fly gunzip, mount ntfs-3g ro,norecover (raw platter), list /e14 and the
# rk_* targets, then umount + cleanup. One image at a time (guest ~10G free).
# Usage: e31-autopsy.sh [arms...]   default: 20 21 22 23
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
G=/d/yzd/qemu-ci
cd "$T" || exit 2
exec > "$T/e31-autopsy.log" 2>&1

GUEST_INNER='
LOOP=$(sudo losetup -P -f --show /tmp/c31.img) || { echo "LOOPSETUP-FAIL"; exit 9; }
sudo mkdir -p /mnt/e31
echo "RO:"
sudo mount -t ntfs-3g -o ro,norecover ${LOOP}p1 /mnt/e31 2>&1 | head -1
echo "E14-LS:"
ls /mnt/e31/e14/ 2>&1 | sort | tr "\n" ";"
echo ""
echo "E14-RK-GLOB:"
ls -d /mnt/e31/e14/rk_* 2>&1 | sort | tr "\n" ";"
echo ""
sudo umount /mnt/e31 >/dev/null 2>&1
sudo losetup -d $LOOP >/dev/null 2>&1
rm -f /tmp/c31.img
'

for arm in ${*:-20 21 22 23}; do
  for cut in worst best; do
    f="e31-crash-a${arm}-${cut}.img"
    [ -f "$f" ] || { echo "MISS $f"; continue; }
    echo "==== a${arm}-${cut} $(date '+%H:%M:%S') ===="
    $G/gssh.sh "rm -f /tmp/c31.img" >/dev/null 2>&1
    gzip -1c "$f" | $G/gssh.sh "gunzip -c > /tmp/c31.img && sync" || { echo "XFER-FAIL $f"; continue; }
    echo "SENT"
    $G/gssh.sh "$GUEST_INNER" 2>&1 | head -14
  done
done
echo "==== E31-AUTOPSY-DONE $(date '+%H:%M:%S') ===="