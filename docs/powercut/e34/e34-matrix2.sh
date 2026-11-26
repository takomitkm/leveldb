#!/usr/bin/env bash
# E34 matrix runner v2 (host side, on the QEMU rig).
#   usage: e34-matrix2.sh <arm> <op> <topo> <n-trials> <start-index>
#
# Same four phases as e34-matrix.sh (trial / journal last-FLUSH replay /
# Windows recovery boot / capture), but each trial is finalised in place:
# the pre-recovery $LogFile is decoded, then the pre-recovery image and the
# journal are gzip-compressed and the raw 4 GiB files removed.  Peak disk per
# trial drops from ~8 GiB to ~40 MiB, so a 20-trial batch fits the rig.
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
export PATH="/d/yzd/MSYS2/ucrt64/bin:/d/yzd/MSYS2/usr/bin:/usr/bin:$PATH"
cd "$T" || exit 2
ARM=${1:?arm}; OP=${2:?op}; TOPO=${3:?topo}; N=${4:?trials}; START=${5:?start}
OUT="e34-matrix/$ARM-$OP-$TOPO"
mkdir -p "$OUT"
exec > "$T/e34-matrix-$ARM-$OP-$TOPO-$START.log" 2>&1
echo "==== E34 v2 arm=$ARM op=$OP topo=$TOPO trials=$N start=$START $(date '+%m-%d %H:%M:%S') ===="

wait_marker() {
  local i alive
  for i in $(seq 1 120); do
    grep -aql 'E34DONE' "$1" 2>/dev/null && { echo "  E34DONE at $((i*5))s"; return 0; }
    grep -aql '"ev":"fatal"' "$1" 2>/dev/null && { echo "  FATAL at $((i*5))s"; return 2; }
    alive=$(powershell.exe -NoProfile -Command "if (Get-Process -Id $2 -ErrorAction SilentlyContinue) { 'y' } else { 'n' }" 2>/dev/null | tr -d '\r\n ')
    [ "$alive" = "n" ] && { echo "  VM-DIED-EARLY at $((i*5))s"; return 3; }
    sleep 5
  done
  echo "  TIMEOUT-10min no E34DONE"; return 4
}

for k in $(seq "$START" $((START+N-1))); do
  TAG="$ARM-$OP-$TOPO-$k"
  D="$OUT/$TAG"
  echo "-- trial $TAG $(date '+%H:%M:%S')"
  mkdir -p "$D"
  VM="$D/vm-raw.img"; JRN="$D/journal.img"; LOGVOL="$D/logvol-trial.img"
  CUT="$D/vm-prerecover.img"; RVM="$D/vm-recover.img"; RLOG="$D/logvol-recover.img"
  rm -f "$VM" "$JRN" "$LOGVOL" "$CUT" "$RVM" "$RLOG"
  cp e34-base.img "$VM" || { echo "  COPY-BASE-FAIL"; continue; }
  cp e34log.img "$LOGVOL"
  truncate -s 4G "$JRN"
  OUT1=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e34-launch2.ps1" -Disk "$VM" -Log "$JRN" -LogDisk "$LOGVOL" -Iso pe-e34N.iso -ArmArg "E34ARM=$ARM;OP=$OP;TOPO=$TOPO;TRIAL=$k;MODE=trial" -Port 4497 -SerialOut "$D/serial-trial.log" 2>&1)
  PID=$(printf '%s' "$OUT1" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
  if [ -z "$PID" ]; then echo "  NO-PID"; continue; fi
  wait_marker "$LOGVOL" "$PID"; RC1=$?
  echo "  phase1 rc=$RC1"
  powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
  sleep 2

  # power-cut image: journal replayed up to the last FLUSH barrier
  python e34-journal-replay.py replay "$JRN" "$VM" "$CUT" LAST 2>&1 | tail -2
  if [ ! -s "$CUT" ]; then echo "  REPLAY-FAIL"; continue; fi

  # recovery boot: Windows performs its own NTFS log replay
  cp "$CUT" "$RVM"; cp e34log.img "$RLOG"
  OUT2=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e34-launch2.ps1" -Disk "$RVM" -Log "$JRN" -LogDisk "$RLOG" -Iso pe-e34N.iso -ArmArg "E34ARM=$ARM;OP=$OP;TOPO=$TOPO;TRIAL=$k;MODE=recover" -Port 4498 -SerialOut "$D/serial-recover.log" 2>&1)
  PID2=$(printf '%s' "$OUT2" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
  if [ -n "$PID2" ]; then
    wait_marker "$RLOG" "$PID2"; RC2=$?
    echo "  phase3 rc=$RC2"
    powershell.exe -NoProfile -Command "Stop-Process -Id $PID2 -Force -ErrorAction SilentlyContinue"
    sleep 2
  else
    echo "  phase3 NO-PID"; RC2=9
  fi

  strings -n 20 "$LOGVOL" 2>/dev/null | grep '^{"ev"' > "$D/manifest-trial.jsonl"
  strings -n 20 "$RLOG"   2>/dev/null | grep '^{"ev"' > "$D/manifest-recover.jsonl"
  echo "  manifests: trial=$(wc -l < "$D/manifest-trial.jsonl") recover=$(wc -l < "$D/manifest-recover.jsonl")"

  # --- in-place finalise: decode, compress, drop the raw images -------------
  NAME=$(python -c "import json
for line in open(r'$D/manifest-trial.jsonl',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='trial_start':
        print(ev.get('name') or ''); break" 2>/dev/null)
  if [ -n "$NAME" ] && [ -s "$CUT" ]; then
    python e34-analysis/analysis/e34-preimage.py --image "$CUT" \
      --volume-offset 1048576 --expect "$NAME" --out "$D/preimage.json" 2>&1 | tail -1
  fi
  if [ -s "$D/preimage.json" ] && [ -s "$CUT" ]; then
    gzip -1 -c "$CUT" > "$D/vm-prerecover.img.gz" && rm -f "$CUT"
  fi
  if [ -s "$JRN" ]; then
    gzip -1 -c "$JRN" > "$D/journal.img.gz" && rm -f "$JRN"
  fi
  rm -f "$VM" "$RVM" "$RLOG"
  echo "  size=$(du -sh "$D" | cut -f1) free=$(df -h /d | tail -1 | awk '{print $4}')"
  echo "  done $(date '+%H:%M:%S')"
done
echo "==== MATRIX2 DONE $(date '+%H:%M:%S') ===="
