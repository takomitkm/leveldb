#!/usr/bin/env bash
# E22 - Linux kernel ntfs3 (Paragon) on NTFS: does fsync(file) alone persist
# the directory entry, or is fsync(dir) needed (ext4-style), or neither?
# Same three arms as E19 (which answered this for ntfs-3g/FUSE), but the
# power cut is modelled deterministically with dm-log-writes + offline
# replay, and the autopsy mounts the replayed image BOTH ways:
#   ntfs-3g ro,norecover  = raw on-platter state (no journal replay)
#   ntfs3 ro              = the kernel driver, which replays $LogFile
# so the run also measures whether journal recovery resurrects the file.
#
# Runs on ubuntu-latest (passwordless sudo).  If the kernel modules are
# missing it prints a capability report and exits cleanly so the artifact
# still uploads.
set -u
W="$(cd "$(dirname "$0")" && pwd)"   # .../docs/powercut in the checkout
S=/mnt/e22
sudo mkdir -p "$S" && sudo chown "$(id -u)" "$S"
cd "$S" || exit 2
exec > >(tee "$S/e22-main.log") 2>&1

echo "=== E22: ntfs3 capability probe ==="
uname -a
KREL="$(uname -r)"
echo "-- /proc/filesystems (ntfs*) --"
grep -i ntfs /proc/filesystems || echo "(none)"

sudo modprobe ntfs3 2>&1 && echo MODPROBE-NTFS3-OK || echo MODPROBE-NTFS3-FAIL
sudo modprobe dm_mod 2>&1 || true
sudo modprobe dm_log_writes 2>&1 && echo MODPROBE-DMLOG-OK || echo MODPROBE-DMLOG-FAIL

if ! grep -q ntfs3 /proc/filesystems || ! ls /sys/module/dm_log_writes >/dev/null 2>&1; then
  echo "-- trying linux-modules-extra-$KREL --"
  sudo apt-get update -qq 2>&1 | tail -1
  sudo apt-get install -y -qq "linux-modules-extra-$KREL" 2>&1 | tail -2
  sudo depmod -a 2>/dev/null
  sudo modprobe ntfs3 2>&1 && echo MODPROBE2-NTFS3-OK || echo MODPROBE2-NTFS3-FAIL
  sudo modprobe dm_log_writes 2>&1 && echo MODPROBE2-DMLOG-OK || echo MODPROBE2-DMLOG-FAIL
fi

NTFS3_OK=no;  grep -q ntfs3 /proc/filesystems && NTFS3_OK=yes

# --- minimal dm-log-writes repro: is the target usable on this kernel? ---
echo "=== dm-log-writes minimal repro ==="
truncate -s 64M mini-log.img && truncate -s 64M mini-data.img
ML=$(sudo losetup -f --show mini-log.img)
MD=$(sudo losetup -f --show mini-data.img)
MSZ=$(sudo blockdev --getsz "$MD")
MINI_OK=no
for ARGS in "log-writes $ML $MD" "log-writes $MD $ML"; do
  sudo dmsetup remove e22mini 2>/dev/null
  OUT=$(sudo dmsetup create e22mini --table "0 $MSZ $ARGS" 2>&1)
  RC=$?
  echo "  dmsetup create [$ARGS] rc=$RC out=[$OUT]"
  if [ $RC -eq 0 ]; then
    MINI_OK=yes
    echo "  MINIMAL-REPRO-OK with [$ARGS]"
    sudo dmsetup remove e22mini
    break
  fi
done
sudo losetup -d "$ML" "$MD" 2>/dev/null
rm -f mini-log.img mini-data.img
echo "MINI-OK=$MINI_OK"
if [ "$MINI_OK" != yes ]; then
  echo "CAPABILITY-FAIL: dm-log-writes target exists but dmsetup rejects the table on this kernel."
  exit 0
fi
DMLOG_OK=no;  ls /sys/module/dm_log_writes >/dev/null 2>&1 && DMLOG_OK=yes
echo "CAPABILITY: ntfs3=$NTFS3_OK dm-log-writes=$DMLOG_OK"
if [ "$NTFS3_OK" != yes ] || [ "$DMLOG_OK" != yes ]; then
  echo "CAPABILITY-FAIL: cannot run the experiment on this kernel; see above."
  find /lib/modules/"$KREL" -name 'ntfs3*' -o -name 'dm-log-writes*' 2>/dev/null | head -5
  exit 0
fi

echo
echo "=== E22: packages ==="
sudo apt-get install -y -qq ntfs-3g >/dev/null 2>&1 || sudo apt-get install -y ntfs-3g 2>&1 | tail -1
sudo apt-get install -y -qq dmsetup 2>&1 | tail -1
sudo dmsetup targets 2>&1 | head -4
which mkfs.ntfs gcc python3 dmsetup

echo
echo "=== E22: probe build ==="
gcc -O2 -o "$S/probe" "$W/e19lin.c" && echo BUILD-OK || { echo BUILD-FAIL; exit 3; }

run_arm() {  # $1 = arm (0 fsync-file, 1 +fsync-dir, 2 teeth)
  A="$1"
  echo
  echo "================ ARM $A ================"
  rm -f a$A-data.img a$A-log.img a$A-replay-*.img
  truncate -s 64M a$A-data.img
  truncate -s 64M a$A-log.img
  # ubuntu runners have every loop device occupied by snap squashfs mounts
  sudo losetup -D 2>/dev/null || true
  for X in 0 1 2 3 4 5 6 7; do
    [ -e /dev/loop$X ] || sudo mknod /dev/loop$X b 7 $X 2>/dev/null || true
  done
  LD=$(sudo losetup -f --show a$A-data.img) || { echo LOSETUP-DATA-FAIL; return 1; }
  LL=$(sudo losetup -f --show a$A-log.img) || { echo LOSETUP-LOG-FAIL; sudo losetup -d "$LD" 2>/dev/null; return 1; }
  SZ=$(sudo blockdev --getsz "$LD")
  sudo dmsetup remove e22dev 2>/dev/null
  # dm-log-writes constructor is <log_device> <data_device> - log FIRST
  OUT=$(sudo dmsetup create e22dev --table "0 $SZ log-writes $LL $LD" 2>&1)
  RC=$?
  echo "dmsetup create rc=$RC SZ=$SZ LL=$LL LD=$LD out=[$OUT]"
  if [ $RC -ne 0 ]; then echo DM-CREATE-FAIL; return 1; fi
  echo "dm table: $(sudo dmsetup table e22dev | head -1)" 
  sudo mkfs.ntfs -F -q -L E22 /dev/mapper/e22dev || { echo MKFS-FAIL; return 1; }
  sudo mkdir -p /mnt/e22a
  sudo mount -t ntfs3 /dev/mapper/e22dev /mnt/e22a || { echo MOUNT-NTFS3-FAIL; sudo dmsetup remove e22dev; return 1; }
  sudo "$S/probe" /mnt/e22a "$A" 0; echo "probe rc=$?"
  sudo sync
  sudo umount /mnt/e22a
  sudo dmsetup remove e22dev
  sudo losetup -d "$LD" "$LL"
  echo "-- image fingerprints (first 8 bytes) --"
  echo "data-img: $(od -A n -t x1 -N 8 a$A-data.img | tr -s ' ')"
  echo "log-img : $(od -A n -t x1 -N 8 a$A-log.img | tr -s ' ')"
  echo "-- replay + autopsy --"
  python3 "$W/e22_replay.py" "$S/a$A-data.img" "$S/a$A-log.img" "$S/a$A"
}

run_arm 0
run_arm 1
run_arm 2

echo
echo "================ SUMMARY ================"
cat "$S"/a*-verdict.txt 2>/dev/null
echo "E22-DONE"
