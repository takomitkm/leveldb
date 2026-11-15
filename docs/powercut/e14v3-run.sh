#!/usr/bin/env bash
# E14 v3 single-arm runner (host side).  Usage: e14v3-run.sh <arm> [reps]
# 1. two copies of the master base: vm copy (gets modified by QEMU) and
#    pristine replay base (kept untouched for offline crash-image building)
# 2. boot; poll the block log for "E14DONE"; force-kill on hit or 12 min
# 3. artifacts kept: e14v3-base-a<arm>.img / e14v3-vm-a<arm>.img / e14v3-log-a<arm>.img
set -u
T=<TASKDIR>
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
cd "$T" || exit 2
ARM="${1:?arm}"
REPS="${2:-3}"
exec > "$T/e14v3-run-a$ARM.log" 2>&1

echo "==== ARM=$ARM REPS=$REPS  $(date '+%m-%d %H:%M:%S') ===="
VM=e14v3-vm-a$ARM.img
BASE=e14v3-base-a$ARM.img
LOG=e14v3-log-a$ARM.img
rm -f "$VM" "$LOG" e14v3-serial.log
cp e14v3-base.img "$BASE" || { echo COPY-FAIL; exit 3; }
cp e14v3-base.img "$VM"
truncate -s 4G "$LOG"   # blklogwrites log node needs an existing file

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e14v3-launch.ps1" -Disk "$VM" -Log "$LOG" -ArmArg "E14ARM=$ARM;REPS=$REPS" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

SEEN=0
for i in $(seq 1 72); do   # 72 x 10s = 12 min
  if grep -aq 'E14DONE' "$LOG" 2>/dev/null; then SEEN=1; echo "DONE-MARKER at $((i*10))s"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  if [ "$ALIVE" = "n" ]; then echo "VM-DIED-EARLY at $((i*10))s (crash? -no-reboot exit)"; break; fi
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-12min no E14DONE"

echo "== force kill (never graceful: a clean quit would flush the guest cache) =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "log-records-visible=$(grep -aoc 'E14' "$LOG" 2>/dev/null || echo 0)"
echo "log-size=$(stat -c %s "$LOG")"
echo "serial-bytes=$(stat -c %s e14v3-serial.log 2>/dev/null || echo 0)"
echo "== kept: $BASE $VM $LOG =="
echo "==== ARM=$ARM END $(date '+%H:%M:%S') ===="