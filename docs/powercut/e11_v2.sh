#!/usr/bin/env bash
# E11 v2: fixed harness — uploaded/compiled artifacts are synced before use,
# every trial verifies the probe before running, and probe rc is recorded.
# Arm sync:   rename + fsync(dir)  -> expect SURVIVED every trial
# Arm nosync: rename only          -> expect at least one LOST (teeth)
set -uo pipefail
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
Q=<QEMUDIR>
GSSH="$Q/gssh.sh"
GSCP="$Q/gscp.sh"
TASK=<TASKDIR>
RESULTS=$TASK/e11-v2-results.tsv
LOG=$TASK/e11-v2.log
printf 'trial\tarm\tprobe_rc\toutcome\n' > "$RESULTS"

boot_and_wait() {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File '<QEMUDIR>/launch.ps1' >/dev/null 2>&1
  for i in $(seq 1 36); do
    "$GSSH" 'echo up' >/dev/null 2>&1 && return 0
    sleep 5
  done
  return 1
}

kill_vm() {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$TASK/stop-vm.ps1" >/dev/null 2>&1
  sleep 1
}

# ensure probe exists, is valid, and is durable; returns 0 when ready
ensure_probe() {
  local size
  size=$("$GSSH" 'stat -c %s ~/e11/probe 2>/dev/null || echo 0' 2>/dev/null | tr -dc 0-9)
  if [ "${size:-0}" -lt 10000 ]; then
    "$GSCP" "$TASK/e11-probe.c" ci@<guest>:/home/ci/e11/ >/dev/null 2>&1 || return 2
    "$GSSH" 'cd ~/e11 && gcc -O2 -o probe e11-probe.c && sync' >/dev/null 2>&1 || return 3
    size=$("$GSSH" 'stat -c %s ~/e11/probe 2>/dev/null || echo 0' 2>/dev/null | tr -dc 0-9)
    [ "${size:-0}" -ge 10000 ] || return 4
  fi
  return 0
}

run_arm() {
  local arm="$1" trials="$2" t size rc out
  for t in $(seq 1 "$trials"); do
    if ! boot_and_wait; then
      printf '%s\t%s\t-\tBOOT-FAIL\n' "$t" "$arm" >> "$RESULTS"
      continue
    fi
    if ! ensure_probe; then
      printf '%s\t%s\t-\tPROBE-UNAVAILABLE\n' "$t" "$arm" >> "$RESULTS"
      continue
    fi
    "$GSSH" "~/e11/probe ~/e11 $arm $t" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
      printf '%s\t%s\t%s\tPROBE-FAIL\n' "$t" "$arm" "$rc" >> "$RESULTS"
      continue
    fi
    sleep 0.2
    kill_vm
    if ! boot_and_wait; then
      printf '%s\t%s\t%s\tBOOT2-FAIL\n' "$t" "$arm" "$rc" >> "$RESULTS"
      continue
    fi
    out=$("$GSSH" "cd ~/e11; if [ -f t_${arm}_${t}.to ] && [ \"\$(cat t_${arm}_${t}.to 2>/dev/null)\" = renamed-content ] && [ ! -f t_${arm}_${t}.from ]; then echo SURVIVED; else echo LOST; fi" 2>/dev/null)
    printf '%s\t%s\t%s\t%s\n' "$t" "$arm" "$rc" "$out" >> "$RESULTS"
  done
}

echo "E11V2-START $(date)"
"$GSSH" 'mkdir -p ~/e11' >/dev/null 2>&1
run_arm sync 5
run_arm nosync 8
echo "== E11V2-DONE"
cat "$RESULTS"
