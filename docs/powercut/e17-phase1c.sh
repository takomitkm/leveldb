#!/usr/bin/env bash
# E17 phase 1c (host): pull the rebuilt ISO back (guest-side gz was made
# manually after the genisoimage fix); verify sizes.
set -u
T=<TASKDIR>
G=<QEMUDIR>
cd "$T" || exit 2
exec > "$T/e17-phase1c.log" 2>&1

$G/gssh.sh 'cd /home/ci/e13 && gzip -1c pe-e17.iso > pe-e17.iso.gz && stat -c %s pe-e17.iso.gz' || { echo GZ-FAIL; exit 3; }
$G/gscp.sh ci@<guest>:/home/ci/e13/pe-e17.iso.gz "$T/" || { echo FETCH-ISO-FAIL; exit 4; }
gunzip -c pe-e17.iso.gz > pe-e17.iso
ls -la pe-e17.iso e17-base.img
echo "E17-PHASE1C-DONE"
