#!/usr/bin/env bash
# E34.1 single trial: guest cuts at flush_end via a fixed-offset trigger;
# the host watches the E34LOG image for that token and force-kills QEMU.
#   usage: e34-run-immediate.sh <arm> <trial>
set -uo pipefail
T=/d/yzd/leveldb-e2e-yhPf93BW
export PATH="/d/yzd/MSYS2/ucrt64/bin:/d/yzd/MSYS2/usr/bin:/usr/bin:$PATH"
cd "$T" || exit 2
ARM=${1:?arm}; N=${2:?trial}
TAG="$ARM-imm-$N"
D="e34-imm/$TAG"; mkdir -p "$D"
exec > "$D/run.log" 2>&1
echo "==== E34.1 arm=$ARM trial=$N $(date '+%H:%M:%S.%N') ===="

VM="$D/vm-raw.img"; JRN="$D/journal.img"; LOGVOL="$D/logvol.img"
CUT="$D/vm-prerecover.img"; RVM="$D/vm-recover.img"; RLOG="$D/logvol-recover.img"
rm -f "$VM" "$JRN" "$LOGVOL" "$CUT" "$RVM" "$RLOG"
cp e34-base.img "$VM" || { echo COPY-FAIL; exit 3; }
cp /c/Users/YiZhidi/e34log2.img "$LOGVOL"
truncate -s 4G "$JRN"

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e34-launch2.ps1" \
  -Disk "$VM" -Log "$JRN" -LogDisk "$LOGVOL" -Iso pe-e34I.iso \
  -ArmArg "E34ARM=$ARM;OP=create;TOPO=large;TRIAL=$N;MODE=immediate" \
  -Port 4499 -SerialOut "$D/serial-trial.log" 2>&1)
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

# --- immediate trigger watcher + native kill ------------------------------
# The watcher itself calls TerminateProcess on detection: no PowerShell cold
# start (~2.3 s) on the critical path.  It records detection, kill request and
# kill completion with a monotonic clock in the receipt.
T0=$(python -c 'import time;print(time.monotonic_ns())')
python e34-trigger-watch.py "$LOGVOL" 3114496 600 token \
  --kill-pid "$PID" --out "$D/kill-receipt.json" > "$D/detect.txt" 2>&1
RC=$?
echo "watch rc=$RC t0=$T0"
cat "$D/detect.txt"
if [ "$RC" != 0 ]; then
  echo "TRIGGER-TIMEOUT"
  powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
fi
sleep 2
echo "$T0" > "$D/timing.txt"
cat "$D/kill-receipt.json" >> "$D/timing.txt" 2>/dev/null || true

# journal replay to the last FLUSH barrier (the power-cut image)
python e14_analyze.py replay "$JRN" "$VM" "$CUT" LAST 2>&1 | tail -2
if [ ! -s "$CUT" ]; then echo REPLAY-FAIL; exit 5; fi

# Recovery boot: Windows' own NTFS replay, external manifest
cp "$CUT" "$RVM"; cp /c/Users/YiZhidi/e34log2.img "$RLOG"
OUT2=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e34-launch2.ps1" \
  -Disk "$RVM" -Log "$JRN" -LogDisk "$RLOG" -Iso pe-e34I.iso \
  -ArmArg "E34ARM=$ARM;OP=create;TOPO=large;TRIAL=$N;MODE=recover" \
  -Port 4501 -SerialOut "$D/serial-recover.log" 2>&1)
PID2=$(printf '%s' "$OUT2" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
if [ -n "$PID2" ]; then
  LOGVOL2="$RLOG"
  python e34-trigger-watch.py "$LOGVOL2" 3114496 600 change \
    --kill-pid "$PID2" --out "$D/kill-receipt-rec.json" \
    > "$D/detect-rec.txt" 2>&1 || echo "recover trigger timeout"
  sleep 2
fi

# Extract manifests and decode pre-recovery log
strings -n 20 "$LOGVOL" 2>/dev/null | grep '^{"ev"' > "$D/manifest-trial.jsonl"
strings -n 20 "$RLOG"   2>/dev/null | grep '^{"ev"' > "$D/manifest-recover.jsonl"
NAME=$(python -c "import json
for line in open(r'$D/manifest-trial.jsonl',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='trial_start': print(ev.get('name') or ''); break" 2>/dev/null)
[ -n "$NAME" ] && python e34-analysis/analysis/e34-preimage.py --image "$CUT" \
  --volume-offset 1048576 --expect "$NAME" --out "$D/preimage.json" 2>&1 | tail -1
gzip -1 -c "$CUT" > "$D/vm-prerecover.img.gz" && rm -f "$CUT"
gzip -1 -c "$JRN" > "$D/journal.img.gz" && rm -f "$JRN"
rm -f "$VM" "$RVM" "$RLOG"
echo "==== DONE $(date '+%H:%M:%S.%N') ===="
