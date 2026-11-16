#!/usr/bin/env bash
# E17-W10 single-arm runner (host side).  Usage: e17w10-arm.sh <arm> [reps]
# Full Windows 10 Pro 22H2 guest (os-golden overlay, discarded), fresh data
# disk per arm under blklogwrites; poll for E17DONE (35 min — full-Windows
# boot is slow under TCG); force-kill; keep base/vm/log artifacts.
set -u
T=<TASKDIR>
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
cd "$T" || exit 2
ARM="${1:?arm}"
REPS="${2:-3}"
exec > "$T/e17w10-run-a$ARM.log" 2>&1

echo "==== W10 ARM=$ARM REPS=$REPS  $(date '+%m-%d %H:%M:%S') ===="
VM=e17w10-arm-a$ARM.qcow2
BASE=e17w10-base-a$ARM.img
LOG=e17w10-log-a$ARM.img
rm -f "$VM" "$LOG" e17w10-serial-a$ARM.log e17w10-datadisk-a$ARM.img
cp e17-base.img "$BASE" || { echo COPY-FAIL; exit 3; }
cp e17-base.img e17w10-datadisk-a$ARM.img
truncate -s 4G "$LOG"

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e17w10-launch.ps1" -Arm "$ARM" -Reps "$REPS" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

SEEN=0
for i in $(seq 1 210); do  # 210 x 10s = 35 min
  if grep -aq 'E17DONE' "$LOG" 2>/dev/null; then SEEN=1; echo "DONE-MARKER at $((i*10))s $(date '+%H:%M:%S')"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  [ "$ALIVE" = "n" ] && { echo "VM-DIED-EARLY at $((i*10))s"; break; }
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-35min no E17DONE"

echo "== force kill (never graceful: a clean quit would flush the guest cache) =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "marker: $(grep -ao 'E17DONE arm=[0-9]* reps=[0-9]* how=[A-Z] errs=[0-9,-]*' "$LOG" | head -1)"
echo "log-size=$(stat -c %s "$LOG")"
echo "== kept: $BASE e17w10-datadisk-a$ARM.img $LOG =="
echo "==== W10 ARM=$ARM END $(date '+%H:%M:%S') ===="
