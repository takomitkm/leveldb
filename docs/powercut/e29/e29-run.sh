#!/usr/bin/env bash
# E29 host-side runner (runs on the school Windows host under MSYS2).
# Usage: e29-run.sh <arm> [reps]
# Same protocol as E17: one arm per boot, force kill after E29DONE,
# keep images for offline blklogwrites cut + ntfs-3g autopsy.
set -u
TASKDIR="<TASKDIR>"
MSYSBIN="<MSYS2>/ucrt64/bin"
export PATH="$MSYSBIN:<MSYS2>/usr/bin:$PATH"
cd "$TASKDIR" || exit 2
ARM="${1:?arm}"
REPS="${2:-3}"
exec > "$TASKDIR/e29-run-a$ARM.log" 2>&1
echo "==== ARM=$ARM REPS=$REPS $(date '+%m-%d %H:%M:%S') ===="
VM="e29-vm-a$ARM.img"
BASE="e29-base-a$ARM.img"
LOG="e29-log-a$ARM.img"
rm -f "$VM" "$LOG" e29-serial.log
cp e29-base.img "$BASE" || { echo COPY-FAIL; exit 3; }
cp e29-base.img "$VM"
truncate -s 4G "$LOG"
OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$TASKDIR/e29-launch.ps1" -Disk "$VM" -Log "$LOG" -ArmArg "E29ARM=$ARM;REPS=$REPS" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"
SEEN=0
for i in $(seq 1 72); do
  if grep -aq 'E29DONE' "$LOG" 2>/dev/null; then SEEN=1; echo "DONE-MARKER at $((i*10))s"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  if [ "$ALIVE" = "n" ]; then echo "VM-DIED-EARLY at $((i*10))s"; break; fi
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-12min no E29DONE"
echo "== force kill =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "log-size=$(stat -c %s "$LOG")"
echo "serial-bytes=$(stat -c %s e29-serial.log 2>/dev/null || echo 0)"
echo "== kept: $BASE $VM $LOG =="
echo "==== ARM=$ARM END $(date '+%H:%M:%S') ===="
