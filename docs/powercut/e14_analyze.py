#!/usr/bin/env python3
"""E14 offline analyzer — QEMU 11.1 blklogwrites log decoder + crash replay.

Confirmed against qemu v11.1.2 block/blklogwrites.c:
  super   (28 bytes, padded to 512): magic u64, version u64,
                                      nr_entries u64, sectorsize u32
  entry   (32 bytes, padded to sectorsize then followed by data):
                                      sector u64, nr_sectors u64,
                                      flags u64, data_len u64
  flags: 1=FLUSH, 2=FUA, 4=DISCARD, 8=MARK
  Entries start at log sector 1 (byte offset = sectorsize).

Usage:
  python e14_analyze.py selftest
  python e14_analyze.py decode   <log.img> [max-records]
  python e14_analyze.py replay   <log.img> <base.img> <out.img> <cut>
      cut = LAST -> keep everything up to and including the last FLUSH entry
                    (drop all writes after it = volatile cache lost)
      cut = N    -> keep first N entries
"""
import struct, sys, os

MAGIC = 0x6A736677736872
ENTRY_SZ = 32
F_WRITE = 0  # implicit: nr_sectors>0 and not DISCARD
F_FLUSH = 1
F_FUA = 2
F_DISCARD = 4
F_MARK = 8


def read_records(path, max_records=None):
    with open(path, 'rb') as f:
        hdr = f.read(64)
        magic, ver, n, ss = struct.unpack_from('<QQQI', hdr, 0)
        if magic != MAGIC:
            raise SystemExit(f'bad magic {magic:#x}')
        print(f'header magic={magic:#x} version={ver} nr_entries={n} sectorsize={ss}')
        if max_records is not None:
            n = min(n, max_records)
        recs = []
        for i in range(n):
            pos = ss + i * 0  # computed below by scanning
            break
        # entries are variable-sized; scan from sector 1
        f.seek(ss)
        for i in range(n):
            rh = f.read(ENTRY_SZ)
            if len(rh) < ENTRY_SZ:
                print(f'TRUNCATED header at {i}')
                break
            sector, nr, fl, dlen = struct.unpack('<QQQQ', rh)
            # skip padding to end of log sector for the entry header
            f.seek(ss - ENTRY_SZ, 1)
            data = b''
            if nr and not (fl & F_DISCARD):
                need = nr * ss
                data = f.read(need)
                if len(data) < need:
                    print(f'TRUNCATED data at {i}: want {need} got {len(data)}')
                    break
            elif fl & F_DISCARD:
                pass
            recs.append((sector, nr, fl, data))
        return ss, recs


def decode(path, max_records=None):
    ss, recs = read_records(path, max_records)
    for i, (sector, nr, fl, data) in enumerate(recs):
        tags = ''.join(c for c, m in [('F', F_FLUSH), ('U', F_FUA),
                                      ('D', F_DISCARD), ('M', F_MARK)] if fl & m)
        print(f'  [{i:5d}] off={sector*ss:>12d} bytes={nr*ss:>8d} '
              f'flags={fl:#04x} {tags or "-"} '
              f'data0={data[:4].hex() if data else ""}')


def replay(log_path, base_path, out_path, cut):
    ss, recs = read_records(log_path)
    if cut == 'LAST':
        flushes = [i for i, r in enumerate(recs) if r[2] & F_FLUSH]
        sel = (flushes[-1] + 1) if flushes else 0
        if not flushes:
            print('NO FLUSH in log -> nothing durable, cut at 0')
    else:
        sel = int(cut)
    print(f'replay: total={len(recs)} selected={sel} cut={cut}')
    # stream-copy base then apply writes
    with open(base_path, 'rb') as fb, open(out_path, 'wb') as fo:
        while True:
            b = fb.read(16 * 1024 * 1024)
            if not b:
                break
            fo.write(b)
    applied = 0
    with open(out_path, 'r+b') as fo:
        for sector, nr, fl, data in recs[:sel]:
            if nr and not (fl & F_DISCARD) and data:
                fo.seek(sector * ss)
                fo.write(data)
                applied += len(data)
        fo.flush()
        os.fsync(fo.fileno())
    print(f'applied {applied} bytes -> {out_path} size={os.path.getsize(out_path)}')


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix='e14-selftest-')
    log = os.path.join(d, 'log.img'); base = os.path.join(d, 'base.img')
    ss = 512
    hdr = struct.pack('<QQQI', MAGIC, 1, 3, ss) + b'\0' * (ss - 28)
    def entry(sector, nr, fl, data=b''):
        e = struct.pack('<QQQQ', sector, nr, fl, len(data))
        e += b'\0' * (ss - ENTRY_SZ)
        return e + data
    body = entry(0, 1, 0, b'A' * ss) + entry(0, 0, F_FLUSH) + \
           entry(1, 1, 0, b'B' * ss)
    open(log, 'wb').write(hdr + body)
    open(base, 'wb').write(b'\0' * (4 * ss))
    out = os.path.join(d, 'out.img')
    replay(log, base, out, 'LAST')
    got = open(out, 'rb').read(2 * ss)
    assert got[:ss] == b'A' * ss, 'pre-flush write must survive'
    assert got[ss:2*ss] == b'\0' * ss, 'post-flush write must be lost'
    print('SELFTEST PASS: pre-flush A survived, post-flush B lost')
    out2 = os.path.join(d, 'out2.img')
    replay(log, base, out2, '1')
    assert open(out2, 'rb').read(ss) == b'A' * ss
    print('SELFTEST PASS: explicit cut=1')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'selftest':
        selftest()
    elif cmd == 'decode':
        decode(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 200)
    elif cmd == 'replay':
        replay(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__); sys.exit(1)
# ---- marker-aware analysis (E14 probe writes raw marker sectors) ----------

def load_records(path):
    """Return (sector_size, [(sector, nr, flags, data), ...])."""
    import struct as _s
    with open(path, 'rb') as f:
        hdr = f.read(64)
        magic, ver, n, ss = _s.unpack_from('<QQQI', hdr, 0)
        if magic != MAGIC:
            raise SystemExit(f'bad magic {magic:#x} in {path}')
        f.seek(ss)
        recs = []
        for _ in range(n):
            rh = f.read(ENTRY_SZ)
            if len(rh) < ENTRY_SZ:
                break
            sector, nr, fl, dlen = _s.unpack_from('<QQQQ', rh, 0)
            f.seek(ss - ENTRY_SZ, 1)
            data = b''
            if nr and not (fl & F_DISCARD):
                need = nr * ss
                data = f.read(need)
                if len(data) < need:
                    break
            recs.append((sector, nr, fl, data))
        return ss, recs


def find_markers(recs):
    """Locate E14MARK sectors; return list of (index, tag, seq)."""
    out = []
    for i, (sector, nr, fl, data) in enumerate(recs):
        if data and b'E14MARK' in data[:64]:
            txt = data[:120].split(b'\x00', 1)[0].decode('ascii', 'replace')
            parts = txt.split()
            tag = parts[1] if len(parts) > 1 else '?'
            seq = parts[2] if len(parts) > 2 else '0'
            out.append((i, tag, seq))
    return out


def report(log_path):
    ss, recs = load_records(log_path)
    print(f'== log: {len(recs)} records, {ss}B sectors, '
          f'{sum(1 for r in recs if r[2]&F_WRITE)} flush(s) ==')
    marks = find_markers(recs)
    print(f'== markers: {len(marks)}')
    for i, tag, seq in marks:
        print(f'   rec#{i:5d}  {tag} seq={seq}')
    # Between consecutive markers, list what happened (writes + flushes).
    print('== segment summary (marker -> marker) ==')
    for a in range(len(marks) - 1):
        i0, tag0, _ = marks[a]
        i1, tag1, _ = marks[a + 1]
        seg = recs[i0 + 1:i1]
        nw = sum(1 for r in seg if r[1] and not (r[2] & F_DISCARD))
        nf = sum(1 for r in seg if r[2] & F_FLUSH)
        bytes_w = sum(r[1] * ss for r in seg if r[1] and not (r[2] & F_DISCARD))
        tail = ''.join('F' if r[2] & F_FLUSH else ('W' if r[1] else '.')
                       for r in seg[:40])
        print(f'   {tag0:32s} -> {nw:4d}W {nf:3d}F {bytes_w:>9d}B  [{tail}]')
