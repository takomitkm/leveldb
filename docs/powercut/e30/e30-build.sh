#!/usr/bin/env bash
# E30 build (guest): MINIMAL-DELTA rebuild for the NT operation-level FWT probe.
# Same v8 rule: tree17's boot files stay BYTE-IDENTICAL. Only changes:
#   - delete setup.exe entries
#   - add e30pe.exe as /e13pe.exe  (NT probe, no rightkit PEs needed)
# Output: pe-e30b.iso  (E30 = NT op-level FWT, arms 34-37)
set -u
cd /home/ci/e13 || exit 2

echo "== 1. compile e30pe.exe (Debian mingw g++, static) =="
x86_64-w64-mingw32-g++ -O2 -static -Wno-multichar -o e30pe.exe e30pe.cpp 2>&1 | tail -5
rc=${PIPESTATUS[0]}
echo "compile_rc=$rc"
[ "$rc" -ne 0 ] && exit 3
ls -la e30pe.exe

echo "== 2. binary sanity strings =="
strings -a e30pe.exe | grep -aoE 'E30ARM|e30-create-ok|e30-rename-ok|e30-delete-ok|e30-composite-ok|E17DONE' | sort | uniq -c

echo "== 3. assemble tree (copy tree17 verbatim) =="
rm -rf e30btree && cp -a tree17 e30btree || exit 4

echo "== 4. WIM updates: ONLY setup deletes + e30pe.exe as /e13pe.exe =="
for IDX in 1 2; do
  W=e30btree/sources/boot.wim
  wimlib-imagex update "$W" "$IDX" --command="delete /setup.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="delete /sources/setup.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="delete /e13pe.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="add /home/ci/e13/e30pe.exe /e13pe.exe" >/dev/null 2>&1
  echo "IDX $IDX probe=$(wimlib-imagex dir "$W" "$IDX" | grep -ac '^/e13pe.exe')"
done

echo "== 5. boot files byte-identical to tree17 (winpeshl.ini + startnet.cmd, IDX2) =="
rm -rf /tmp/e30ref /tmp/e30new && mkdir -p /tmp/e30ref /tmp/e30new
wimlib-imagex extract tree17/sources/boot.wim 2 /Windows/System32/winpeshl.ini --dest=/tmp/e30ref --no-acls >/dev/null 2>&1
wimlib-imagex extract tree17/sources/boot.wim 2 /Windows/System32/startnet.cmd --dest=/tmp/e30ref --no-acls >/dev/null 2>&1
wimlib-imagex extract e30btree/sources/boot.wim 2 /Windows/System32/winpeshl.ini --dest=/tmp/e30new --no-acls >/dev/null 2>&1
wimlib-imagex extract e30btree/sources/boot.wim 2 /Windows/System32/startnet.cmd --dest=/tmp/e30new --no-acls >/dev/null 2>&1
for f in winpeshl.ini startnet.cmd; do
  h1=$(md5sum "/tmp/e30ref/$f" 2>/dev/null | cut -d' ' -f1)
  h2=$(md5sum "/tmp/e30new/$f" 2>/dev/null | cut -d' ' -f1)
  if [ -n "$h1" ] && [ "$h1" = "$h2" ]; then echo "UNCHANGED $f md5=$h1"
  else echo "MUTATED $f ref=$h1 new=$h2"; fi
done

echo "== 6. rebuild ISO =="
rm -f pe-e30b.iso
genisoimage -sysid "" -A "" -V "E30V1" -d -N -b etfsboot.com -no-emul-boot \
  -c boot.catalog -hide etfsboot.com -hide boot.catalog -quiet \
  -o pe-e30b.iso e30btree/ >/dev/null 2>&1
echo "iso=$(stat -c %s pe-e30b.iso)"
sha256sum pe-e30b.iso | cut -c1-24
echo E30-BUILD-DONE
