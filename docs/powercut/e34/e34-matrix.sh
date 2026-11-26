#!/usr/bin/env bash
# E34 matrix runner (host side, on the QEMU rig).
#   usage: e34-matrix.sh <arm> <op> <topo> <n-trials> [start-index]
#
# Each trial runs these phases:
#   1. trial    boot WinPE with the E34 probe on a fresh copy of the E34
#               baseline disk; wait for E34DONE on the independent E34LOG
#               volume; force-kill QEMU -- that is the power cut
#   2. replay   rebuild the disk image the way a power cut leaves it: the
#               QEMU blklogwrites journal is replayed up to the LAST FLUSH
#               barrier, so writes the guest never flushed are lost.  This
#               is the power-cut model of E34-PLAN section 3.1; copying the
#               raw disk file would keep unflushed host-cache data and
#               would not model a power cut at all.
#   3. recover  boot the SAME WinPE on a copy of the replayed image with
#               MODE=recover, so Windows performs its own NTFS log replay,
#               and report the recovered namespace over the external channel
#   4. capture  keep the pre-recovery image, the journal and both manifests
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
export PATH="/d/yzd/MSYS2/ucrt64/bin:/d/yzd/MSYS2/usr/bin:/usr/bin:$PATH"
cd "$T" || exit 2
ARM=${1:?arm}; OP=${2:?op}; TOPO=${3:?topo}; N=${4:?trials}; START=${5:-1}
OUT="e34-matrix/$ARM-$OP-$TOPO"
mkdir -p "$OUT"
exec > "$T/e34-matrix-$ARM-$OP-$TOPO.log" 2>&1
echo "==== E34 matrix arm=$ARM op=$OP topo=$TOPO trials=$N start=$START $(date '+%m-%d %H:%M:%S') ===="

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

  # phase 2: rebuild the power-cut image (journal replay up to the last flush)
  python e34-journal-replay.py replay "$JRN" "$VM" "$CUT" LAST 2>&1 | tail -2
  if [ ! -s "$CUT" ]; then echo "  REPLAY-FAIL"; continue; fi

  # phase 3: recovery (Windows replays the log itself)
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
  echo "  manifest lines: trial=$(wc -l < "$D/manifest-trial.jsonl") recover=$(wc -l < "$D/manifest-recover.jsonl")"
  rm -f "$VM" "$RVM" "$RLOG"
  echo "  done $(date '+%H:%M:%S')"
done
echo "==== MATRIX DONE $(date '+%H:%M:%S') ===="
