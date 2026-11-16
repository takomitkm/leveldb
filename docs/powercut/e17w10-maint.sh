#!/usr/bin/env bash
# E17-W10 maintenance runner: one boot, installer arm 90 writes the new probe
# into the golden's C:\e14\e14pe3.exe; host polls the block log for the
# E17INSTALLED marker then force-kills (the golden keeps the change).
set -u
T=<TASKDIR>
export PATH="<HOSTDIR>/MSYS2/ucrt64/bin:<HOSTDIR>/MSYS2/usr/bin:$PATH"
cd "$T" || exit 2
exec > "$T/e17w10-maint.log" 2>&1

echo "==== E17-W10 MAINT $(date '+%m-%d %H:%M:%S') ===="
rm -f e17w10-maint-data.img e17w10-maint-log.img e17w10-maint-serial.log
cp e17-base.img e17w10-maint-data.img || { echo COPY-FAIL; exit 3; }
truncate -s 4G e17w10-maint-log.img

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e17w10-maint.ps1" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

SEEN=0
for i in $(seq 1 60); do  # 60 x 10s = 10 min
  if grep -aq 'E17INSTALLED' e17w10-maint-log.img 2>/dev/null; then SEEN=1; echo "INSTALLED at $((i*10))s"; break; fi
  ALIVE=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $PID -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
  [ "$ALIVE" = "n" ] && { echo "VM-DIED-EARLY at $((i*10))s"; break; }
  sleep 10
done
[ "$SEEN" = 0 ] && echo "TIMEOUT-10min no E17INSTALLED"

powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
echo "marker: $(grep -ao 'E17INSTALLED size=[0-9]* os=[A-Z]:' e17w10-maint-log.img | head -1)"
echo "==== E17-W10 MAINT END $(date '+%H:%M:%S') ===="
