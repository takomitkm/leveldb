#!/usr/bin/env bash
# E34 - one crash trial on the QEMU rig (host side).
#
# The rig is the one E14/E17/E29-E31 used: QEMU with the data disk behind
# blklogwrites, so every write and every flush is journalled.  The guest runs
# the e34 probe; the controller on the host records the external manifest and
# cuts the power at the configured moment.
#
# The crash itself is "terminate the guest without a graceful shutdown".
# Whether that is a physical power cut depends on the storage backend's
# volatile-cache model, which the caller must state; the trial is only
# reported as a power-cut trial when that model is verified (E34-PLAN.md
# section 3.1).  With blklogwrites the worst-case state can be reconstructed
# offline from the journal, which is the conservative model.
#
# Usage:
#   e34-trial.sh --qemu QEMU --img base.img --work workdir \
#       --arm C --op create --topology large --reps 1 \
#       [--crash-delay-ms 0] [--guest-ip 10.0.2.15] [--ctrl-port 45000]
#
# The host must be able to reach the guest on the controller port; the
# default topology uses QEMU user-mode networking with hostfwd.
set -euo pipefail

QEMU=""
BASE_IMG=""
WORK=""
ARM="C"
OP="create"
TOPOLOGY="large"
REPS=1
CRASH_DELAY_MS=0
CTRL_PORT=45000
GUEST_IP="10.0.2.15"
LOG_IMG=""
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --qemu) QEMU="$2"; shift 2;;
    --img) BASE_IMG="$2"; shift 2;;
    --work) WORK="$2"; shift 2;;
    --arm) ARM="$2"; shift 2;;
    --op) OP="$2"; shift 2;;
    --topology) TOPOLOGY="$2"; shift 2;;
    --reps) REPS="$2"; shift 2;;
    --crash-delay-ms) CRASH_DELAY_MS="$2"; shift 2;;
    --ctrl-port) CTRL_PORT="$2"; shift 2;;
    --guest-ip) GUEST_IP="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[ -n "$QEMU" ] && [ -n "$BASE_IMG" ] && [ -n "$WORK" ] || {
  echo "usage: e34-trial.sh --qemu ... --img ... --work ..." >&2; exit 2; }
[ -x "$QEMU" ] || { echo "qemu not executable: $QEMU" >&2; exit 2; }
[ -f "$BASE_IMG" ] || { echo "baseline image missing: $BASE_IMG" >&2; exit 2; }

mkdir -p "$WORK"
TRIAL_ID="$(date -u +%Y%m%dT%H%M%SZ)-${ARM}-${OP}-${TOPOLOGY}"
TRIAL_DIR="$WORK/$TRIAL_ID"
mkdir -p "$TRIAL_DIR"

# The volume under test is a copy of the baseline: each trial starts from the
# same state, and the crashed copy is what gets analysed.
DISK_IMG="$TRIAL_DIR/data.img"
LOG_IMG="$TRIAL_DIR/journal.img"
cp "$BASE_IMG" "$DISK_IMG"
: > "$LOG_IMG"

MANIFEST="$TRIAL_DIR/manifest.jsonl"
SUMMARY="$TRIAL_DIR/manifest-summary.json"
PIDFILE="$TRIAL_DIR/guest.pid"

echo "e34: trial $TRIAL_ID (arm=$ARM op=$OP topology=$TOPOLOGY)"

# The controller starts first and waits for the guest.
python3 "$(dirname "$0")/e34-crashctl.py" \
  --port "$CTRL_PORT" --manifest "$MANIFEST" --summary "$SUMMARY" \
  --crash-point flush_end --delay-ms "$CRASH_DELAY_MS" \
  --kill-pid-file "$PIDFILE" --timeout 900 \
  > "$TRIAL_DIR/controller.log" 2>&1 &
CTL_PID=$!
sleep 1

QEMU_ARGS=(
  -machine pc -accel tcg,thread=multi -cpu max -smp 4 -m 2048
  -no-reboot -display none
  -blockdev "driver=file,node-name=d0,filename=$DISK_IMG"
  -blockdev "driver=file,node-name=l0,filename=$LOG_IMG"
  -blockdev driver=blklogwrites,node-name=data,file=d0,log=l0,log-sector-size=512,log-super-update-interval=1
  -device ide-hd,drive=data,bus=ide.0,unit=0
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${CTRL_PORT}-:45000
  -device e1000,netdev=n0
)

# The probe is injected into the guest image by the launcher (see
# E34-PLAN.md section 8 for the channel wiring); the environment below is
# passed through the SMBIOS serial string, as in the E17 rig.
export E34_ARM="$ARM"
export E34_OP="$OP"
export E34_TOPOLOGY="$TOPOLOGY"
export E34_REP="$REPS"
export E34_CTRL_PORT=45000

"$QEMU" "${QEMU_ARGS[@]}" \
  -smbios "type=1,serial=E34ARM=$ARM;OP=$OP;TOPO=$TOPOLOGY;CTRL=$CTRL_PORT" \
  -drive "file=$TRIAL_DIR/probe.iso,if=ide,media=cdrom,index=1" \
  -boot order=d \
  > "$TRIAL_DIR/qemu.log" 2>&1 &
QEMU_PID=$!

# The controller kills the guest when the crash point is reached; it learns the
# guest pid from this file, because the guest starts after the controller.
sleep 2
echo "$QEMU_PID" > "$PIDFILE"

# Wait for the controller to finish.
wait "$CTL_PID" || true

# Make sure the guest is gone.
kill -9 "$QEMU_PID" 2>/dev/null || true
wait "$QEMU_PID" 2>/dev/null || true

echo "e34: trial done; artifacts in $TRIAL_DIR"
if [ "$KEEP" -eq 0 ]; then
  :
fi
