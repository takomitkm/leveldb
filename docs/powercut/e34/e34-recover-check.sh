#!/usr/bin/env bash
# E34 - recovery check on a COPY of the crashed image (Linux / WSL).
#
# Uses ntfs-3g twice, like the E17 autopsy:
#   1) ro,norecover : the raw on-disk namespace (what survived without replay)
#   2) rw           : ntfs-3g replays $LogFile like NTFS mount would
# Then lists the target directory and checks the expected names.
#
# Usage: e34-recover-check.sh <image> <expect-name> [more names...]
set -uo pipefail
IMG="${1:?image}"
shift
EXPECT=("$@")
command -v ntfs-3g >/dev/null || { echo "ntfs-3g missing"; exit 2; }
command -v losetup >/dev/null || { echo "losetup missing"; exit 2; }

echo "== E34 recovery check: $IMG =="
echo "expected names: ${EXPECT[*]}"

LOOP=$(losetup -P -f --show "$IMG") || { echo LOSETUP-FAIL; exit 3; }
echo "loop=$LOOP"
PART="${LOOP}p1"
# The rig's data image is a 1MiB-offset MBR with one NTFS partition; when the
# image is a bare NTFS volume, use the loop itself.
blkid "$PART" >/dev/null 2>&1 || PART="$LOOP"
echo "partition=$PART"

mkdir -p /mnt/e34check
echo "== RO-NORECOVER (raw platter state) =="
umount /mnt/e34check 2>/dev/null
mount -t ntfs-3g -o ro,norecover "$PART" /mnt/e34check 2>&1 | head -3
RAW=$(ls -1 /mnt/e34check/e14 2>/dev/null | tr '\n' ' ')
echo "raw e14 listing: $RAW"
for n in "${EXPECT[@]}"; do
  [ -e "/mnt/e34check/e14/$n" ] && echo "  raw PRESENT $n" || echo "  raw ABSENT  $n"
done
umount /mnt/e34check 2>/dev/null

echo "== RW-REPLAY (journal replay on a copy) =="
mount -t ntfs-3g "$PART" /mnt/e34check 2>&1 | head -3
REC=$(ls -1 /mnt/e34check/e14 2>/dev/null | tr '\n' ' ')
echo "recovered e14 listing: $REC"
for n in "${EXPECT[@]}"; do
  [ -e "/mnt/e34check/e14/$n" ] && echo "  recovered PRESENT $n" || echo "  recovered ABSENT  $n"
done
umount /mnt/e34check 2>/dev/null
losetup -d "$LOOP" 2>/dev/null
echo "E34-RECOVERY-CHECK-DONE"
