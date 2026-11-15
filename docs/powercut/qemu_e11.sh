#!/usr/bin/env bash
# E11: QEMU instant-power-off durability matrix on the Debian guest.
# Arm sync:   rename + fsync(dir)  -> expect SURVIVED every trial
# Arm nosync: rename only          -> expect at least one LOST (teeth)
# Each trial: boot guest -> run probe -> kill QEMU (virtual power cut)
#             -> boot again -> inspect persistence.
set -uo pipefail
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
Q=<QEMUDIR>
GSSH="$Q/gssh.sh"
GSCP="$Q/gscp.sh"
TASK=<TASKDIR>
RESULTS=$TASK/e11-results.tsv
printf 'trial\tarm\toutcome\n' > "$RESULTS"

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

need_setup=yes
run_arm() {
  local arm="$1" trials="$2"
  local t
  for t in $(seq 1 "$trials"); do
    if ! boot_and_wait; then
      printf '%s\t%s\tBOOT-FAIL\n' "$t" "$arm" >> "$RESULTS"
      continue
    fi
    if [ "$need_setup" = yes ]; then
      "$GSSH" 'mkdir -p ~/e11' >/dev/null 2>&1
      "$GSCP" "$TASK/e11-probe.c" ci@<guest>:/home/ci/e11/ >/dev/null 2>&1
      "$GSSH" 'cd ~/e11 && gcc -O2 -o probe e11-probe.c' >/dev/null 2>&1 || {
        printf '%s\t%s\tCOMPILE-FAIL\n' "$t" "$arm" >> "$RESULTS"
        continue
      }
      need_setup=no
    fi
    "$GSSH" "~/e11/probe ~/e11 $arm $t" >/dev/null 2>&1
    sleep 0.2
    kill_vm
    if ! boot_and_wait; then
      printf '%s\t%s\tBOOT2-FAIL\n' "$t" "$arm" >> "$RESULTS"
      continue
    fi
    local out
    out=$("$GSSH" "cd ~/e11; if [ -f t_${arm}_${t}.to ] && [ \"\$(cat t_${arm}_${t}.to 2>/dev/null)\" = renamed-content ] && [ ! -f t_${arm}_${t}.from ]; then echo SURVIVED; else echo LOST; fi" 2>/dev/null)
    printf '%s\t%s\t%s\n' "$t" "$arm" "$out" >> "$RESULTS"
  done
}

run_arm sync 6
run_arm nosync 10
echo "== E11-DONE"
cat "$RESULTS"
