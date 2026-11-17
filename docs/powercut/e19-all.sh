#!/usr/bin/env bash
# E19 all-arms runner: ONE boot, THREE independent NTFS trials (one per disk),
# ONE power cut.  Arms: 0 = fsync(file) only; 1 = +fsync(dirfd); 2 = teeth.
set -u
T=<TASKDIR>
G=<QEMUDIR>
export PATH="<HOSTDIR>/MSYS2/ucrt64/bin:<HOSTDIR>/MSYS2/usr/bin:$PATH"
cd "$T" || exit 2
exec > "$T/e19-all.log" 2>&1

echo "==== E19 ALL $(date '+%m-%d %H:%M:%S') ===="
$G/gssh.sh 'echo up' >/dev/null 2>&1 && {
  powershell.exe -NoProfile -Command "
    Get-CimInstance Win32_Process -Filter \"Name='qemu-system-x86_64.exe'\" |
      Where-Object { \$_.CommandLine -match 'ci-disk' } |
      ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }
    Write-Output stopped" >/dev/null 2>&1
  sleep 3
}

for a in 0 1 2; do
  rm -f e19-datadisk-a$a.img e19-log-a$a.img
  cp e17-base.img e19-datadisk-a$a.img || { echo COPY-FAIL; exit 3; }
  truncate -s 4G e19-log-a$a.img
done

OUT=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$T/e19-all-launch.ps1" 2>&1)
echo "$OUT"
PID=$(printf '%s' "$OUT" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$PID" ] && { echo NO-PID; exit 4; }
echo "vm-pid=$PID"

UP=0
for i in $(seq 1 40); do   # 40 x 15s = 10 min (root fs may be dirty -> fsck)
  $G/gssh.sh 'echo up' >/dev/null 2>&1 && { UP=1; echo "guest up at try $i"; break; }
  sleep 15
done
[ "$UP" = 0 ] && { echo GUEST-UP-FAIL; exit 5; }

$G/gssh.sh 'mkdir -p /home/ci/e19' >/dev/null 2>&1
$G/gscp.sh e19lin.c ci@<guest>:/home/ci/e19/ || { echo SCP-FAIL; exit 6; }

$G/gssh.sh "cd /home/ci/e19 && gcc -O2 -o e19lin e19lin.c && sync && echo COMPILED" 2>&1 | tail -1

$G/gssh.sh "sudo bash -c '
mkdir -p /mnt/e19a /mnt/e19b /mnt/e19c
mount -t ntfs-3g /dev/vdb1 /mnt/e19a
mount -t ntfs-3g /dev/vdc1 /mnt/e19b
mount -t ntfs-3g /dev/vdd1 /mnt/e19c
sync
echo MOUNTED
'" 2>&1 | tail -1

$G/gssh.sh "sudo bash -c '
/home/ci/e19/e19lin /mnt/e19a 0 0; echo RC0=\$?
/home/ci/e19/e19lin /mnt/e19b 1 0; echo RC1=\$?
/home/ci/e19/e19lin /mnt/e19c 2 0; echo RC2=\$?
'" 2>&1 | tail -3

sleep 1   # E11 protocol window
echo "== power cut =="
powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue"
sleep 2
for a in 0 1 2; do echo "log-a$a-size=$(stat -c %s e19-log-a$a.img)"; done
echo "==== E19 ALL END $(date '+%H:%M:%S') ===="
