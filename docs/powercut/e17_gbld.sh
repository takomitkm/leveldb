#!/usr/bin/env bash
# E17 ISO build (runs INSIDE Debian CI guest): inject e17pe.exe as /e13pe.exe
# (winpeshl launches that name), delete setup.exe, rebuild ISO.
# Derived from e14v3_gbld.sh with the probe name and volume label changed.
set -uo pipefail
cd /home/ci/e13 || exit 2
SRC=/home/ci/e12/tree
WIM=/home/ci/e12/boot-orig.wim
[ -f /home/ci/e13/e17pe.exe ] || { echo MISSING-EXE; exit 3; }
rm -rf tree && mkdir -p tree/boot tree/sources
cp "$SRC/boot/etfsboot.com" tree/etfsboot.com
cp "$SRC/bootmgr" tree/bootmgr
cp "$SRC/boot/bcd" tree/boot/bcd
cp "$SRC/boot/boot.sdi" tree/boot/boot.sdi
cp "$WIM" tree/sources/boot.wim
for IDX in 1 2; do
  wimlib-imagex update tree/sources/boot.wim "$IDX" --command="delete /setup.exe" >/dev/null 2>&1
  wimlib-imagex update tree/sources/boot.wim "$IDX" --command="delete /sources/setup.exe" >/dev/null 2>&1
  wimlib-imagex update tree/sources/boot.wim "$IDX" --command="add /home/ci/e13/e17pe.exe /e13pe.exe" >/dev/null 2>&1
  wimlib-imagex update tree/sources/boot.wim "$IDX" --command="add /home/ci/e13/winpeshl.ini /Windows/System32/winpeshl.ini" >/dev/null 2>&1
  echo "IDX $IDX probe=$(wimlib-imagex dir tree/sources/boot.wim $IDX | grep -ac /e13pe.exe)"
done
rm -f pe-e17.iso
genisoimage -sysid "" -A "" -V "E17V1" -d -N -b etfsboot.com -no-emul-boot \
  -c boot.catalog -hide etfsboot.com -hide boot.catalog -quiet -o pe-e17.iso tree/ >/dev/null 2>&1
echo "iso=$(stat -c %s pe-e17.iso)"
gzip -1c pe-e17.iso > pe-e17.iso.gz
echo "gz=$(stat -c %s pe-e17.iso.gz)"
echo E17-GBLD-DONE
