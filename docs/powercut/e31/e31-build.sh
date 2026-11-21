#!/usr/bin/env bash
# E31 build (guest): MINIMAL-DELTA rebuild for the rightkit public-API wrapper.
#
# Same v8 rule as e29-build8.sh: tree17's boot files stay BYTE-IDENTICAL to
# the working E17 build. Only changes:
#   - delete setup.exe entries
#   - add the WRAPPER probe as /e13pe.exe  (e29pe.cpp -> e30-e29pe.exe)
#   - add the four static rightkit PEs
# No winpeshl.ini / startnet.cmd injection (tree17's proven ones are kept).
#
# Output: pe-e30.iso  (E31 = rightkit public-API single ops via wrapper arms 20-23)
set -u
cd /home/ci/e13 || exit 2

echo "== 1. compile wrapper (Debian mingw g++, static) =="
x86_64-w64-mingw32-g++ -O2 -static -Wno-multichar -o e30-e29pe.exe e29pe.cpp 2>&1 | tail -5
rc=${PIPESTATUS[0]}
echo "compile_rc=$rc"
[ "$rc" -ne 0 ] && exit 3
ls -la e30-e29pe.exe

echo "== 2. binary sanity strings =="
strings -a e30-e29pe.exe | grep -aoE 'E29ARM=|E29DONE|rightkit-create-dir|rk_%d|E17ARM=' | sort | uniq -c

echo "== 3. assemble tree (copy tree17 verbatim) =="
rm -rf e30tree && cp -a tree17 e30tree || exit 4

echo "== 4. WIM updates: ONLY setup deletes + wrapper + rightkit PEs =="
for IDX in 1 2; do
  W=e30tree/sources/boot.wim
  wimlib-imagex update "$W" "$IDX" --command="delete /setup.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="delete /sources/setup.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="delete /e13pe.exe" >/dev/null 2>&1
  wimlib-imagex update "$W" "$IDX" --command="add /home/ci/e13/e30-e29pe.exe /e13pe.exe" >/dev/null 2>&1
  for f in rightkit-create-dir.exe rightkit-rename.exe rightkit-delete.exe rightkit-write.exe; do
    wimlib-imagex update "$W" "$IDX" --command="add /home/ci/e13/$f /$f" >/dev/null 2>&1
  done
  echo "IDX $IDX probe=$(wimlib-imagex dir "$W" "$IDX" | grep -ac '^/e13pe.exe') rk=$(wimlib-imagex dir "$W" "$IDX" | grep -ac '^/rightkit-')"
done

echo "== 5. boot files byte-identical to tree17 (winpeshl.ini + startnet.cmd, IDX2) =="
rm -rf /tmp/e31ref /tmp/e31new && mkdir -p /tmp/e31ref /tmp/e31new
wimlib-imagex extract tree17/sources/boot.wim 2 /Windows/System32/winpeshl.ini --dest=/tmp/e31ref --no-acls >/dev/null 2>&1
wimlib-imagex extract tree17/sources/boot.wim 2 /Windows/System32/startnet.cmd --dest=/tmp/e31ref --no-acls >/dev/null 2>&1
wimlib-imagex extract e30tree/sources/boot.wim 2 /Windows/System32/winpeshl.ini --dest=/tmp/e31new --no-acls >/dev/null 2>&1
wimlib-imagex extract e30tree/sources/boot.wim 2 /Windows/System32/startnet.cmd --dest=/tmp/e31new --no-acls >/dev/null 2>&1
for f in winpeshl.ini startnet.cmd; do
  h1=$(md5sum "/tmp/e31ref/$f" 2>/dev/null | cut -d' ' -f1)
  h2=$(md5sum "/tmp/e31new/$f" 2>/dev/null | cut -d' ' -f1)
  if [ -n "$h1" ] && [ "$h1" = "$h2" ]; then echo "UNCHANGED $f md5=$h1"
  else echo "MUTATED $f ref=$h1 new=$h2"; fi
done

echo "== 6. rebuild ISO =="
rm -f pe-e30.iso
genisoimage -sysid "" -A "" -V "E30V1" -d -N -b etfsboot.com -no-emul-boot \
  -c boot.catalog -hide etfsboot.com -hide boot.catalog -quiet \
  -o pe-e30.iso e30tree/ >/dev/null 2>&1
echo "iso=$(stat -c %s pe-e30.iso)"
sha256sum pe-e30.iso | cut -c1-24
echo E31-BUILD-DONE
