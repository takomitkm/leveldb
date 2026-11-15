#!/usr/bin/env bash
# E17 phase 1 (host): compile probe, push to Debian CI guest, build ISO +
# master base disk inside guest, pull both back.  Mirrors e14v3-phase1.sh;
# the only difference is the probe name and marker tag (E17ARM / E17DONE).
set -u
T=<TASKDIR>
G=<QEMUDIR>
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
cd "$T" || exit 2
exec > "$T/e17-phase1.log" 2>&1

echo "== 1. compile e17pe.exe (MSYS2 ucrt64 g++; probe source sits next to this script)"
g++ -O2 -static -o e17pe.exe e17pe.cpp 2> e17-compile.log || { cat e17-compile.log; exit 3; }
ls -la e17pe.exe

echo "== 2. guest space check"
$G/gssh.sh 'df -h / | tail -1'

echo "== 3. push probe + scripts, verify byte sizes"
LOCAL1=$(stat -c %s e17pe.exe)
$G/gssh.sh 'rm -f /home/ci/e13/e17pe.exe' >/dev/null 2>&1
$G/gscp.sh e17pe.exe e17_gbld.sh e14v3_mkbdata.sh ci@<guest>:/home/ci/e13/ || { echo SCP-FAIL; exit 4; }
$G/gssh.sh 'stat -c %s /home/ci/e13/e17pe.exe'
echo "local exe=$LOCAL1"

echo "== 4. guest: build ISO (reuses e14 tree; injects e17pe.exe as /e13pe.exe)"
$G/gssh.sh 'sed -i "s/e14pe3.exe/e17pe.exe/g; s/E14V3/E17V1/" /home/ci/e13/e17_gbld.sh; bash /home/ci/e13/e17_gbld.sh' 2>&1 | tail -8

echo "== 5. guest: build master base disk (reuse e14v3_mkbdata.sh verbatim)"
$G/gssh.sh 'bash /home/ci/e13/e14v3_mkbdata.sh' 2>&1 | tail -5

echo "== 6. pull ISO + base"
$G/gscp.sh ci@<guest>:/home/ci/e13/pe-e17.iso.gz "$T/" || { echo FETCH-ISO-FAIL; exit 5; }
gunzip -c pe-e17.iso.gz > pe-e17.iso
$G/gscp.sh ci@<guest>:/home/ci/e13/base14.img.gz "$T/" || { echo FETCH-BASE-FAIL; exit 6; }
gunzip -c base14.img.gz > e17-base.img
ls -la pe-e17.iso e17-base.img
echo "E17-PHASE1-DONE"
