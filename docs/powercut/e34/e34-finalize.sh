#!/usr/bin/env bash
# E34 - finish a matrix run on the rig: preserve evidence, decode the
# pre-recovery log, and stage the small analysis set for the local host.
#
# For every trial directory under e34-matrix/<cell>/<tag>/ this:
#   1. gzip-copies vm-prerecover.img into the SSH home directory (kept
#      evidence, ~19 MB instead of 4 GiB) and records its SHA-256;
#   2. runs the pre-recovery $LogFile decode (preimage.json);
#   3. removes the duplicate raw image once both exist.
#
# Usage: bash e34-finalize.sh [cell ...]     (default: every cell)
set -uo pipefail
export PATH=/usr/bin:$PATH
cd /d/yzd/leveldb-e2e-yhPf93BW || exit 2

CELLS=("$@")
[ ${#CELLS[@]} -eq 0 ] && CELLS=(e34-matrix/*/)

for CELL in "${CELLS[@]}"; do
  [ -d "$CELL" ] || continue
  for D in "$CELL"/*/; do
    [ -d "$D" ] || continue
    n=$(echo "$D" | sed 's#e34-matrix/##; s#/$##; s#/#-#g')
    GZ="/c/Users/YiZhidi/e34-pre-$n.gz"
    if [ ! -s "$GZ" ] && [ -s "$D/vm-prerecover.img" ]; then
      gzip -1 -c "$D/vm-prerecover.img" > "$GZ" || { echo "GZ-FAIL $n"; continue; }
    fi
    if [ -s "$GZ" ]; then
      sha256sum "$GZ" >> /c/Users/YiZhidi/e34-pre-sha256.txt
    fi
    if [ -s "$D/vm-prerecover.img" ] && [ ! -s "$D/preimage.json" ]; then
      MAN="$D/manifest-trial.jsonl"
      NAME=$(python -c "import json
for line in open(r'$MAN',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='trial_start':
        print(ev.get('name') or ''); break" 2>/dev/null)
      PARENT=$(python -c "import json
for line in open(r'$MAN',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='dir_info':
        print(ev.get('dir_record') or 0); break" 2>/dev/null)
      ARGS=(--image "$D/vm-prerecover.img" --volume-offset 1048576
            --out "$D/preimage.json")
      [ -n "$NAME" ] && ARGS+=(--expect "$NAME")
      [ -n "$PARENT" ] && [ "$PARENT" != "0" ] && ARGS+=(--parent-record "$PARENT")
      echo "== decode $n ($NAME)"
      python e34-analysis/analysis/e34-preimage.py "${ARGS[@]}" 2>&1 | tail -1
    fi
    # drop the duplicate raw image only when the compressed copy and the
    # decode both exist
    if [ -s "$GZ" ] && [ -s "$D/preimage.json" ] && [ -s "$D/vm-prerecover.img" ]; then
      rm -f "$D/vm-prerecover.img"
    fi
  done
done
echo "D free: $(df -h /d | tail -1 | awk '{print $4}')"
echo E34-FINALIZE-DONE