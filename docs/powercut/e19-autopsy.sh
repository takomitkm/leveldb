#!/usr/bin/env bash
# E19 autopsy: boot CI guest with 3 ro originals + 3 rw replay copies;
# mount each pair (ro,norecover then rw replay) and list /e19.
set -u
T=<TASKDIR>
G=<QEMUDIR>
export PATH="<HOSTDIR>/MSYS2/ucrt64/bin:<HOSTDIR>/MSYS2/usr/bin:$PATH"
cd "$T" || exit 2
exec > "$T/e19-autopsy.log" 2>&1

echo "==== E19 AUTOPSY $(date '+%m-%d %H:%M:%S') ===="
for a in 0 1 2; do cp e19-datadisk-a$a.img e19-autopsy-rw-a$a.img || exit 3; done

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e19-kill.ps1" >/dev/null 2>&1
sleep 3

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e19-autopsy-launch.ps1" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }

UP=0
for i in $(seq 1 40); do
  $G/gssh.sh 'echo up' >/dev/null 2>&1 && { UP=1; echo "guest up at try $i"; break; }
  sleep 15
done
[ "$UP" = 0 ] && { echo GUEST-UP-FAIL; exit 5; }

# device order: vdb,vdc,vdd = ro a0,a1,a2 ; vde,vdf,vdg = rw a0,a1,a2
# NTFS is the first partition of each raw image -> <dev>1
$G/gssh.sh 'sudo bash -c "
RODEVS=(/dev/vdb1 /dev/vdc1 /dev/vdd1)
RWDEVS=(/dev/vde1 /dev/vdf1 /dev/vdg1)
for a in 0 1 2; do
  RO=\${RODEVS[\$a]}; RW=\${RWDEVS[\$a]}
  M=/mnt/e19r\$a; MR=/mnt/e19w\$a
  mkdir -p \$M \$MR
  echo \"== a\$a RO-NORECOVER (\$RO) ==\"
  mount -t ntfs-3g -o ro,norecover \$RO \$M 2>&1 | head -1
  ls \$M/e19/ 2>&1 | grep -av pre_ | sort | tr \"\n\" \";\"; echo \"\"
  umount \$M 2>/dev/null
  echo \"== a\$a RW-REPLAY (\$RW) ==\"
  mount -t ntfs-3g \$RW \$MR 2>&1 | head -1
  ls \$MR/e19/ 2>&1 | grep -av pre_ | sort | tr \"\n\" \";\"; echo \"\"
  umount \$MR 2>/dev/null
done
echo E19-AUTOPSY-DONE
"' 2>&1 | head -40

echo "== kill autopsy VM =="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e19-kill.ps1" >/dev/null 2>&1
echo "==== E19 AUTOPSY END $(date '+%H:%M:%S') ===="
