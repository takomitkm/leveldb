#!/usr/bin/env bash
# E17 single-arm runner (host side).  Usage: e17-run.sh <arm> [reps]
# Same protocol as e14v3-run.sh: two copies of master base (vm copy +
# pristine replay base), boot, poll log for E17DONE, force-kill, keep
# artifacts e17-base-a<arm>.img / e17-vm-a<arm>.img / e17-log-a<arm>.img.
set -u
T=<TASKDIR>
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
cd "$T" || exit 2
ARM="${1:?arm}"
REPS="${2:-3}"
exec > "$T/e17-run-a$ARM.log" 2>&1

echo "==== ARM=$ARM REPS=$REPS  $(date '+%m-%d %H:%M:%S') ===="
VM=e17-vm-a$ARM.img
BASE=e17-base-a$ARM.img
LOG=e17-log-a$ARM.img
rm -f "$VM" "$LOG" e17-serial.log
cp e17-base.img "$BASE" || { echo COPY-FAIL; exit 3; }
cp e17-base.img "$VM"
truncate -s 4G "$LOG"

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e17-launch.ps1" -Disk "$VM" -Log "$LOG" -ArmArg "E17ARM=$ARM;REPS=$REPS" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

SEEN=0
for i in $(seq 1 72); do
  if grep -aq 'E17DONE' "$LOG" 2>/dev/null; then SEEN=1; echo "DONE-MARKER at $((i*10))s"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  if [ "$ALIVE" = "n" ]; then echo "VM-DIED-EARLY at $((i*10))s (crash? -no-reboot exit)"; break; fi
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-12min no E17DONE"

echo "== force kill (never graceful: a clean quit would flush the guest cache) =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "log-records-visible=$(grep -aoc 'E17' "$LOG" 2>/dev/null || echo 0)"
echo "log-size=$(stat -c %s "$LOG")"
echo "serial-bytes=$(stat -c %s e17-serial.log 2>/dev/null || echo 0)"
echo "== kept: $BASE $VM $LOG =="
echo "==== ARM=$ARM END $(date '+%H:%M:%S') ===="
