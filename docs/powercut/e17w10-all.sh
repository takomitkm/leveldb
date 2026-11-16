#!/usr/bin/env bash
# E17-W10 all-arms driver (host, detached via WMI).
set -u
T=<TASKDIR>
cd "$T" || exit 2
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
exec >> "$T/e17w10-all.log" 2>&1
echo "==== E17-W10 START $(date '+%m-%d %H:%M:%S') arms=$* ===="
for arm in "$@"; do
  echo "-- arm $arm begin $(date '+%H:%M:%S')"
  bash e17w10-arm.sh "$arm" 3
  echo "-- arm $arm end $(date '+%H:%M:%S')"
done
echo "==== E17-W10 DONE $(date '+%H:%M:%S') ===="
