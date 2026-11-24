#!/usr/bin/env bash
# Pull each E34 pre-recovery image, dump its raw (no-replay) namespace, and
# discard the image.  Runs one trial at a time so local disk never holds more
# than one 4 GiB image.
set -uo pipefail
D=/s/apps/llm/agentwork/_tmp/e34-ns
NS=/s/apps/llm/agentwork/_tmp/e33-wt/docs/powercut/e34/e34-namespace.py
mkdir -p "$D/img"
: > "$D/namespaces.jsonl"

while read -r gz; do
  [ -n "$gz" ] || continue
  tag=${gz#e34-pre-}; tag=${tag%.gz}
  trial=${tag##*-}
  dir=e34/large
  case "$tag" in *small*) dir=e34/small ;; esac
  batch="$D/batch-get.txt"
  printf 'get %s %s/img/%s.gz\n' "$gz" "$D" "$tag" > "$batch"
  if ! MSYS_NO_PATHCONV=1 sftp -o BatchMode=yes -o ConnectTimeout=60 -b "$batch" school-guest-pc2 >/dev/null 2>&1; then
    echo "{\"tag\":\"$tag\",\"error\":\"pull failed\"}" >> "$D/namespaces.jsonl"
    continue
  fi
  gunzip -f "$D/img/$tag.gz" 2>/dev/null
  out=$(python "$NS" --image "$D/img/$tag" --dir "/$dir" --offset 1048576 2>&1 | tail -1)
  printf '{"tag":"%s","dir":"%s","dump":"%s"}\n' "$tag" "$dir" "$(printf '%s' "$out" | sed 's/"/\\"/g')" >> "$D/namespaces.jsonl"
  echo "$tag -> $out"
  rm -f "$D/img/$tag"
done < "$D/list.txt"
echo NS-DONE