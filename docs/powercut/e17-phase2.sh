#!/usr/bin/env bash
# E17 phase 2: rebuild probe exe with arms 11-15, swap into WIM (tree17 is
# still on the guest), rebuild ISO, pull back.
set -u
T=<TASKDIR>
G=<QEMUDIR>
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
cd "$T" || exit 2
exec > "$T/e17-phase2.log" 2>&1

echo "== 1. compile e17pe.exe (arms 0-15)"
g++ -O2 -static -o e17pe.exe e17pe.cpp 2> e17-compile2.log || { cat e17-compile2.log; exit 3; }
ls -la e17pe.exe

echo "== 2. push exe, guest rebuilds ISO from tree17"
$G/gscp.sh e17pe.exe ci@<guest>:/home/ci/e13/e17pe.exe || { echo SCP-FAIL; exit 4; }
$G/gssh.sh 'cd /home/ci/e13 && for IDX in 1 2; do wimlib-imagex update tree17/sources/boot.wim $IDX --command="delete /e13pe.exe" >/dev/null 2>&1; wimlib-imagex update tree17/sources/boot.wim $IDX --command="add /home/ci/e13/e17pe.exe /e13pe.exe" >/dev/null 2>&1; echo "IDX $IDX probe=$(wimlib-imagex dir tree17/sources/boot.wim $IDX | grep -ac /e13pe.exe)"; done && rm -f pe-e17.iso && genisoimage -sysid "" -A "" -V "E17V2" -d -N -b etfsboot.com -no-emul-boot -c boot.catalog -hide etfsboot.com -hide boot.catalog -quiet -o pe-e17.iso tree17/ && echo "iso=$(stat -c %s pe-e17.iso)" && gzip -1c pe-e17.iso > pe-e17.iso.gz' 2>&1 | tail -5

echo "== 3. pull ISO"
$G/gscp.sh ci@<guest>:/home/ci/e13/pe-e17.iso.gz "$T/" || { echo FETCH-ISO-FAIL; exit 5; }
gunzip -c pe-e17.iso.gz > pe-e17.iso
ls -la pe-e17.iso
echo "E17-PHASE2-DONE"
