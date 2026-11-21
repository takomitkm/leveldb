#!/usr/bin/env bash
# E30 all-arms driver (host, detached via WMI so it survives ssh drops).
set -u
T=/d/yzd/leveldb-e2e-yhPf93BW
cd "$T" || exit 2
export PATH="/d/yzd/MSYS2/ucrt64/bin:/d/yzd/MSYS2/usr/bin:$PATH"
exec >> "$T/e30-all.log" 2>&1
echo "==== E30-ALL START $(date '+%m-%d %H:%M:%S') arms=$* ===="
for arm in "$@"; do
  echo "-- arm $arm begin $(date '+%H:%M:%S')"
  bash e30-run.sh "$arm" 3
  echo "-- arm $arm end $(date '+%H:%M:%S')"
done
echo "==== E30-ALL DONE $(date '+%m-%d %H:%M:%S') ===="
