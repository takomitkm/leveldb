#!/usr/bin/env bash
# E30 autopsy (school host): stream each crash image to the Debian CI guest with
# on-the-fly gunzip, mount ntfs-3g ro,norecover, list /e14 and E30 targets.
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
G=/d/yzd/qemu-ci
cd "$T" || exit 2
exec > "$T/e30-autopsy.log" 2>&1

GUEST_INNER='
LOOP=$(sudo losetup -P -f --show /tmp/c30.img) || { echo "LOOPSETUP-FAIL"; exit 9; }
sudo mkdir -p /mnt/e30
echo "RO:"
sudo mount -t ntfs-3g -o ro,norecover ${LOOP}p1 /mnt/e30 2>&1 | head -1
echo "E14-LS:"
ls /mnt/e30/e14/ 2>&1 | sort | tr "\n" ";"
echo ""
echo "TARGETS:"
ls -d /mnt/e30/e14/e30_* /mnt/e30/e14/src_* /mnt/e30/e14/dst_* /mnt/e30/e14/tmp_* /mnt/e30/e14/final_* 2>&1 | sort | tr "\n" ";"
echo ""
sudo umount /mnt/e30 >/dev/null 2>&1
sudo losetup -d $LOOP >/dev/null 2>&1
rm -f /tmp/c30.img
'

for arm in ${*:-34 35 36 37}; do
  for cut in worst best; do
    f="e30-crash-a${arm}-${cut}.img"
    [ -f "$f" ] || { echo "MISS $f"; continue; }
    echo "==== a${arm}-${cut} $(date '+%H:%M:%S') ===="
    $G/gssh.sh "rm -f /tmp/c30.img" >/dev/null 2>&1
    gzip -1c "$f" | $G/gssh.sh "gunzip -c > /tmp/c30.img && sync" || { echo "XFER-FAIL $f"; continue; }
    echo "SENT"
    $G/gssh.sh "$GUEST_INNER" 2>&1 | head -14
  done
done
echo "==== E30-AUTOPSY-DONE $(date '+%H:%M:%S') ===="