#!/usr/bin/env bash
# E34 rig-side analysis: run the pre-recovery decode for each trial and write
# the JSON next to the artifacts, so classification needs no image pull.
set -u
export PATH=/usr/bin:$PATH
cd /d/yzd/leveldb-e2e-yhPf93BW || exit 2
for D in e34-matrix/*/*/; do
  [ -d "$D" ] || continue
  [ -s "$D/vm-prerecover.img" ] || { echo "skip $D (no image)"; continue; }
  MAN="$D/manifest-trial.jsonl"
  [ -f "$MAN" ] || { echo "skip $D (no manifest)"; continue; }
  NAME=$(python -c "import json,sys
for line in open(r'$MAN',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='trial_start':
        print(ev.get('name') or ''); break")
  PARENT=$(python -c "import json
for line in open(r'$MAN',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except Exception: continue
    if ev.get('ev')=='dir_info':
        print(ev.get('dir_record') or 0); break")
  ARGS=(--image "$D/vm-prerecover.img" --volume-offset 1048576 --out "$D/preimage.json")
  [ -n "$NAME" ] && ARGS+=(--expect "$NAME")
  [ -n "$PARENT" ] && [ "$PARENT" != "0" ] && ARGS+=(--parent-record "$PARENT")
  echo "== $D name=$NAME parent=$PARENT"
  python e34-analysis/analysis/e34-preimage.py "${ARGS[@]}" 2>&1 | tail -1
done
echo E34-ANALYZE-RIG-DONE
