#!/usr/bin/env bash
# E17 phase 1b (host): guest-side ISO build (probe already pushed, old ISO
# already uploaded) + pull ISO and base disk back.
set -u
T=<TASKDIR>
G=<QEMUDIR>
cd "$T" || exit 2
exec > "$T/e17-phase1b.log" 2>&1

echo "== 4. guest: build ISO from pe-e14v3.iso tree"
$G/gssh.sh 'bash /home/ci/e13/e17_gbld.sh' 2>&1 | tail -12

echo "== 5. guest: build master base disk"
$G/gssh.sh 'bash /home/ci/e13/e14v3_mkbdata.sh' 2>&1 | tail -3

echo "== 6. pull ISO + base"
$G/gscp.sh ci@<guest>:/home/ci/e13/pe-e17.iso.gz "$T/" || { echo FETCH-ISO-FAIL; exit 5; }
gunzip -c pe-e17.iso.gz > pe-e17.iso
$G/gscp.sh ci@<guest>:/home/ci/e13/base14.img.gz "$T/" || { echo FETCH-BASE-FAIL; exit 6; }
gunzip -c base14.img.gz > e17-base.img
ls -la pe-e17.iso e17-base.img
echo "E17-PHASE1B-DONE"
