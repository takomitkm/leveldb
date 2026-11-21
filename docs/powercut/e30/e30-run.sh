#!/usr/bin/env bash
# E30 host-side runner (school Windows host, MSYS2).  Usage: e30-run.sh <arm> [reps]
# E30 = NT operation-level FILE_WRITE_THROUGH probe (arms 34-37). Same rig protocol.
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
export PATH="/d/yzd/MSYS2/ucrt64/bin:/d/yzd/MSYS2/usr/bin:$PATH"
cd "$T" || exit 2
ARM="${1:?arm}"
REPS="${2:-3}"
exec > "$T/e30-run-a$ARM.log" 2>&1

echo "==== ARM=$ARM REPS=$REPS  $(date '+%m-%d %H:%M:%S') ===="
VM=e30-vm-a$ARM.img
BASE=e30-base-a$ARM.img
LOG=e30-log-a$ARM.img
rm -f "$VM" "$LOG" e30-serial.log
cp e17-base.img "$BASE" || { echo COPY-FAIL; exit 3; }
cp e17-base.img "$VM"
truncate -s 4G "$LOG"

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e30-launch.ps1" -Disk "$VM" -Log "$LOG" -ArmArg "E30ARM=$ARM;REPS=$REPS" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

SEEN=0
for i in $(seq 1 72); do
  if grep -aq 'E17DONE' "$LOG" 2>/dev/null; then SEEN=1; echo "DONE-MARKER at $((i*10))s"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  if [ "$ALIVE" = "n" ]; then echo "VM-DIED-EARLY at $((i*10))s"; break; fi
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-12min no E17DONE"

echo "== force kill (never graceful) =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "log-size=$(stat -c %s "$LOG")"
echo "serial-bytes=$(stat -c %s e30-serial.log 2>/dev/null || echo 0)"
echo "== kept: $BASE $VM $LOG =="
echo "==== ARM=$ARM END $(date '+%H:%M:%S') ===="
