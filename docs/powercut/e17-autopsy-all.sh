#!/usr/bin/env bash
# E17 autopsy-all v2 (school host): stream each crash image to the Debian CI
# guest with ON-THE-FLY gunzip (single 4GB copy at rest — guest has ~10G free),
# double-mount with ntfs-3g (ro,norecover = raw platter; rw = journal replay),
# list /e14, record verdicts, delete the temp copy. One image at a time.
set -u
T=<TASKDIR>
G=<QEMUDIR>
cd "$T" || exit 2
exec > "$T/e17-autopsy-all.log" 2>&1

GUEST_INNER='
LOOP=$(sudo losetup -P -f --show /tmp/c17.img) || { echo "LOOPSETUP-FAIL"; exit 9; }
sudo mkdir -p /mnt/e17a
echo "RO:"
sudo mount -t ntfs-3g -o ro,norecover ${LOOP}p1 /mnt/e17a 2>&1 | head -1
ls /mnt/e17a/e14/ 2>&1 | sort | tr "\n" ";"
echo ""
echo "D16-RO:"
ls /mnt/e17a/e14/d16/ 2>&1 | sort | tr "\n" ";"
echo ""
sudo umount /mnt/e17a >/dev/null 2>&1
echo "RW:"
sudo mount -t ntfs-3g ${LOOP}p1 /mnt/e17a 2>&1 | head -1
ls /mnt/e17a/e14/ 2>&1 | sort | tr "\n" ";"
echo ""
echo "D16-RW:"
ls /mnt/e17a/e14/d16/ 2>&1 | sort | tr "\n" ";"
echo ""
sudo umount /mnt/e17a >/dev/null 2>&1
sudo losetup -d $LOOP >/dev/null 2>&1
rm -f /tmp/c17.img
'

for arm in ${ARMS:-0 1 2 3 4 5 6 7 8 9 10}; do
  for cut in worst best; do
    f="${PREFIX:-e17}-crash-a${arm}-${cut}.img"
    [ -f "$f" ] || { echo "MISS $f"; continue; }
    echo "==== a${arm}-${cut} $(date '+%H:%M:%S') ===="
    $G/gssh.sh "rm -f /tmp/c17.img" >/dev/null 2>&1
    gzip -1c "$f" | $G/gssh.sh "gunzip -c > /tmp/c17.img && sync" || { echo "XFER-FAIL $f"; continue; }
    echo "PASS1-SENT"
    $G/gssh.sh "$GUEST_INNER" 2>&1 | head -10
  done
done
echo "==== E17-AUTOPSY-ALL-DONE $(date '+%H:%M:%S') ===="
