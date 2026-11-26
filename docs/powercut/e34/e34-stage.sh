#!/usr/bin/env bash
# Stage a finished E34 cell for the analysis host, and record image hashes.
#   usage: e34-stage.sh <cell>     e.g. B-create-large
# For each trial: copy the compressed pre-recovery image to the SSH home as
# e34-pre-<cell>-<tag>.gz and append its SHA-256 to the ledger.  Also tar the
# small artifacts (both manifests + the preimage decode).
set -uo pipefail
CELL=${1:?cell}
D="e34-matrix/$CELL"
[ -d "$D" ] || { echo "no such cell: $D"; exit 2; }
LEDGER=/c/Users/YiZhidi/e34-pre-sha256.txt
n=0
for T in "$D"/*/; do
  [ -d "$T" ] || continue
  tag=$(basename "$T")
  src="$T/vm-prerecover.img.gz"
  [ -s "$src" ] || { echo "skip $tag (no image gz)"; continue; }
  dst="/c/Users/YiZhidi/e34-pre-$CELL-$tag.gz"
  cp -f "$src" "$dst" || { echo "copy-fail $tag"; continue; }
  sha256sum "$dst" >> "$LEDGER"
  n=$((n+1))
done
rm -f "/c/Users/YiZhidi/e34-analysis-$CELL.tar"
tar -cf "/c/Users/YiZhidi/e34-analysis-$CELL.tar" \
  "$D"/*/manifest-trial.jsonl "$D"/*/manifest-recover.jsonl "$D"/*/preimage.json 2>/dev/null
echo "staged $n images; ledger=$(wc -l < "$LEDGER") lines; tar=$(stat -c %s "/c/Users/YiZhidi/e34-analysis-$CELL.tar" 2>/dev/null) bytes"
echo E34-STAGE-DONE
