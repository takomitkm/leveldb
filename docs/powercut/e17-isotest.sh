#!/usr/bin/env bash
# probe isoinfo path forms on pe-e14v3.iso (no Rock Ridge on this ISO)
cd /home/ci/e13 || exit 2
for p in '/BOOTMGR.;1' '/BOOT/BCD.;1' '/BOOT/BOOT.SDI.;1' '/SOURCES/BOOT.WIM.;1' '/ETFSBOOT.COM.;1' '/BOOTMGR' '/BOOT/BCD' '/SOURCES/BOOT.WIM'; do
  s=$(isoinfo -i pe-e14v3.iso -x "$p" 2>/dev/null | wc -c)
  echo "$p => $s"
done
