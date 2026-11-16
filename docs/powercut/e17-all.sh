#!/usr/bin/env bash
# E17 all-arms driver (host, detached via WMI so it survives ssh drops).
# Usage: e17-all.sh <arms...>   (arm order passed by e17-start.ps1)
set -u
T=<TASKDIR>
cd "$T" || exit 2
export PATH="<MSYS2>/ucrt64/bin:<MSYS2>/usr/bin:$PATH"
exec >> "$T/e17-all.log" 2>&1
echo "==== E17-ALL START $(date '+%m-%d %H:%M:%S') arms=$* ===="
for arm in "$@"; do
  echo "-- arm $arm begin $(date '+%H:%M:%S')"
  bash e17-run.sh "$arm" 3
  echo "-- arm $arm end $(date '+%H:%M:%S')"
done
echo "==== E17-ALL DONE $(date '+%m-%d %H:%M:%S') ===="
