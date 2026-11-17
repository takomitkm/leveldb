#!/usr/bin/env python3
"""E22 replay + autopsy for dm-log-writes logs.

The dm-log-writes on-disk log layout (drivers/md/dm-log-writes.c) is the one
QEMU's blklogwrites later copied: superblock (magic u64, version u64,
nr_entries u64, sectorsize u32, padded to sector) at offset 0, then entries
from sector 1 - each a 32-byte header (sector, nr_sectors, flags, data_len)
padded to sectorsize and followed by the data.

Replays a crash image at two cut points:
  worst = everything up to and including the last barrier
          (nr_sectors == 0 and flags != 0) - volatile cache lost
  best  = every entry the device received
Then mounts each image twice:
  ntfs-3g ro,norecover  -> raw on-platter state, no journal replay
  ntfs3  ro             -> the Paragon kernel driver, which replays $LogFile
and lists the trial files.  Needs sudo for losetup/mount.

Usage: e22_replay.py <base.img> <log.img> <out_prefix>
"""
import os
import struct
import subprocess
import sys

base, log, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
SS_DEFAULT = 512


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def read_records(path):
    with open(path, 'rb') as f:
        hdr = f.read(64)
        magic, ver, n, ss = struct.unpack_from('<QQQI', hdr, 0)
        print(f'superblock: magic=0x{magic:x} version={ver} nr_entries={n} sectorsize={ss}')
        recs = []
        f.seek(ss)
        for i in range(n):
            rh = f.read(32)
            if len(rh) < 32:
                break
            sector, nr, fl, dlen = struct.unpack('<QQQQ', rh)
            f.seek(ss - 32, 1)
            data = b''
            if nr and dlen:
                data = f.read(nr * ss)
                if len(data) < nr * ss:
                    break
            recs.append((sector, nr, fl, data))
        return ss, recs


def barrier_indices(recs):
    return [i for i, (s, nr, fl, d) in enumerate(recs) if nr == 0 and fl != 0]


def apply_cut(recs, cut, base_img, out_img, ss):
    with open(base_img, 'rb') as fb, open(out_img, 'wb') as fo:
        while True:
            b = fb.read(16 * 1024 * 1024)
            if not b:
                break
            fo.write(b)
    with open(out_img, 'r+b') as fo:
        for sector, nr, fl, d in recs[:cut]:
            if nr and d:
                fo.seek(sector * ss)
                fo.write(d)


def mount_and_list(img, fstype, opts, mnt):
    sh(['sudo', 'umount', mnt])
    sh(['sudo', 'losetup', '-d', os.popen('sudo losetup -j %s 2>/dev/null | cut -d: -f1' % img).read().strip()]) if False else None
    r = sh(['sudo', 'losetup', '-f', '--show', img])
    loop = r.stdout.strip()
    if not loop:
        return ('LOOPFAIL', r.stderr.strip()[:80])
    try:
        m = sh(['sudo', 'mount', '-t', fstype] + (['-o', opts] if opts else []) + [loop, mnt])
        if m.returncode != 0:
            return ('MOUNTFAIL', (m.stderr or '').strip()[:120])
        ls = sh(['sudo', 'ls', os.path.join(mnt, 'e19')])
        names = sorted(x for x in ls.stdout.split() if x and not x.startswith('pre_'))
        return ('OK', ','.join(names) if names else '(empty)')
    finally:
        sh(['sudo', 'umount', mnt])
        sh(['sudo', 'losetup', '-d', loop])


ss, recs = read_records(log)
bar = barrier_indices(recs)
flags_hist = {}
for _, _, fl, _ in recs:
    flags_hist[fl] = flags_hist.get(fl, 0) + 1
print(f'entries={len(recs)} flags_histogram={flags_hist}')
worst = (bar[-1] + 1) if bar else 0
best = len(recs)
print(f'barriers={len(bar)} worst_cut={worst} best_cut={best}')

results = []
for tag, cut in (('worst', worst), ('best', best)):
    if cut <= 0:
        results.append((tag, 'skipped (cut=0)'))
        continue
    img = f'{prefix}-replay-{tag}.img'
    apply_cut(recs, cut, base, img, ss)
    raw = mount_and_list(img, 'ntfs-3g', 'ro,norecover', '/mnt/e22r')
    kern = mount_and_list(img, 'ntfs3', 'ro', '/mnt/e22k')
    results.append((tag, f'ntfs-3g(raw): {raw[0]} {raw[1]} | ntfs3(kernel): {kern[0]} {kern[1]}'))
    os.remove(img)

with open(f'{prefix}-verdict.txt', 'w') as f:
    arm = os.path.basename(prefix).lstrip('a')
    for tag, line in results:
        f.write(f'arm={arm} cut={tag}: {line}\n')
        print(f'arm={arm} cut={tag}: {line}')
