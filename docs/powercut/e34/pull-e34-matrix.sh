#!/usr/bin/env bash
# Pull a finished E34 matrix from the school rig to the local analysis dir.
# Usage: pull-e34-matrix.sh <arm-op-topo> [local-dir]
set -euo pipefail
TAG=${1:?matrix tag e.g. A-create-large}
LOCAL=${2:-/s/apps/llm/agentwork/_tmp/e34-matrix-local/$TAG}
RIG=/d/yzd/leveldb-e2e-yhPf93BW/e34-matrix/$TAG
mkdir -p "$LOCAL"

# stage compressed images + manifests on the rig's SSH home directory
cat <<EOF | MSYS_NO_PATHCONV=1 ssh -T -o BatchMode=yes school-guest-pc2 'D:/yzd/MSYS2/usr/bin/bash.exe --login -s' >/dev/null 2>&1
cd $RIG
for d in */; do
  n=\$(basename \$d)
  [ -f /c/Users/YiZhidi/e34-pull-\$n.tar ] && continue
  ( cd "\$d" && gzip -1 -c vm-prerecover.img > /c/Users/YiZhidi/e34-pull-\$n.img.gz && \
    tar -cf /c/Users/YiZhidi/e34-pull-\$n.tar e34-pull-\$n.img.gz \
      manifest-trial.jsonl manifest-recover.jsonl ) || echo "stage-fail \$n"
done
echo STAGE-DONE
EOF

# list the staged tarballs
LIST=$(MSYS_NO_PATHCONV=1 sftp -o BatchMode=yes -b /dev/stdin school-guest-pc2 2>/dev/null <<'EOF' | grep -o 'e34-pull-[^ ]*\.tar' | sort -u
ls
EOF
)
BATCH=/s/apps/llm/agentwork/_tmp/e34-tools/e34-pull-batch.txt
: > "$BATCH"
for t in $LIST; do
  printf 'get %s %s\n' "$t" "$LOCAL/$t" >> "$BATCH"
done
MSYS_NO_PATHCONV=1 sftp -o BatchMode=yes -b "$BATCH" school-guest-pc2 >/dev/null 2>&1

cd "$LOCAL"
for t in e34-pull-*.tar; do
  d=${t#e34-pull-}; d=${d%.tar}
  mkdir -p "$d"; tar -xf "$t" -C "$d"
  mv "$d/e34-pull-$d.img.gz" "$d/vm-prerecover.img.gz"
  gunzip -k -f "$d/vm-prerecover.img.gz"
done
echo "pulled $(ls -d */ | wc -l) trials into $LOCAL"
