#!/usr/bin/env python3
"""E17 verdict driver (school host).  Derived from e14v3_verdict.py; the
differences are the marker tags (E17GO/E17DONE), the op-body tags
("e17-a<arm>-r<rep>"), and the arm set 0..7.

For each arm:
  1. parse the block log; locate marker DATA writes and op data writes.
  2. cuts:
       nxt(r)   = GO(r+1) data idx, or DONE idx for the last rep
       worst(r) = barrier_end(nxt(r))  -- guaranteed-durable prefix at op
                                        return (last FLUSH/FUA barrier)
       best(r)  = nxt(r)               -- everything the device received
  3. build crash images (rep 0 only, plus rep 2 worst for last-rep sanity):
       e17-crash-a<arm>-worst.img / -best.img
  4. print log-level verdict summary.

Usage: python e17_verdict.py build   (images for all arms)
       python e17_verdict.py report  (log-level summary only)
"""
import os, re, sys

T = r'<TASKDIR>'
sys.path.insert(0, T)
from e14_analyze import load_records

F_FLUSH, F_FUA, F_DISCARD = 1, 2, 4

ARM_NAMES = {
    0: 'BASELINE_NODIRSYNC (flush file only)',
    1: 'LEVELDB30B34E0/SQLITE/MYSQL/FRANKEN dir-handle flush (create)',
    2: 'SQLITE_S7 relative-path + "." dir flush',
    3: 'LMDB_MDIRSYNC flush + GetFinalPathNameByHandleW',
    4: 'PG_TRANSIENT_FD _commit(fd) route',
    5: 'MYSQL_DELETE_DANCE durable->dance->parent flush',
    6: 'RENAME_WRITE_THROUGH only',
    7: 'MYSQL_CREATEDIR mkdir + parent flush',
    8: 'DELETE_NOFLUSH control (durable->DeleteFileA)',
    9: 'MKDIR_NOFLUSH control (CreateDirectory only)',
    10: 'RENAME_DIRFLUSH durable src->rename->dir flush',
    11: 'CREATE_ROOTFLUSH flush file then root dir only',
    12: 'CREATE_DIRPLUSROOT dir+root flush after create',
    13: 'CREATE_DIRFLUSH_X2 dir flush twice after create',
    14: 'RENAME_DIRPLUSROOT rename then dir+root flush',
    15: 'DELETE_DIRPLUSROOT durable->delete->dir+root flush',
}


def barrier_end(recs, upto):
    pos = 0
    for i in range(min(upto, len(recs))):
        sec, nr, fl, d = recs[i]
        if fl & F_FLUSH:
            pos = i
        elif (fl & F_FUA) and nr:
            pos = i + 1
    return pos


def find_unique(recs, tag):
    return [i for i, (s, nr, fl, d) in enumerate(recs) if d and tag.encode() in d]


def analyze(arm, reps=3, prefix='e17'):
    log = os.path.join(T, f'{prefix}-log-a{arm}.img')
    if not os.path.exists(log):
        print(f'== arm={arm}: no log, skip')
        return None
    ss, recs = load_records(log)
    n = len(recs)
    how = '?'
    go = []
    for r in range(reps):
        idx = find_unique(recs, f'E17GO arm={arm} rep={r}')
        go.append(idx[-1] if idx else None)
        if idx:
            m = re.search(rb'how=(\w)', recs[idx[0]][3])
            if m:
                how = m.group(1).decode()
    done = find_unique(recs, f'E17DONE arm={arm}')
    done = done[-1] if done else None
    # error notes ride in the DONE marker body
    note = ''
    if done is not None:
        mm = re.search(rb'errs=([-\d,]+)( n\d=[^,]*(?:, n\d=.*)?)?', recs[done][3])
        note = (mm.group(1).decode() + (mm.group(2).decode() if mm.group(2) else '')) if mm else ''
    print(f'== arm={arm} ({ARM_NAMES.get(arm, "?")}) how={how} records={n} '
          f'GO_data={go} DONE_data={done} errs[{note}]')

    results = []
    for r in range(reps):
        nxt = go[r+1] if r+1 < reps and go[r+1] is not None else done
        if nxt is None:
            print(f'  rep{r}: NO next marker — skip'); continue
        worst = barrier_end(recs, nxt)
        opdata = find_unique(recs, f'e17-a{arm}-r{r}')
        # arm5 writes its body under a1..name then deletes; the body bytes may
        # reappear; arm7 has no body file (dir only) — namespace-only arm
        inworst = [i for i in opdata if i < worst]
        print(f'  rep{r}: nxt={nxt} worst_prefix={worst} op_data_writes={opdata} '
              f'covered_by_barrier={inworst}')
        results.append((r, nxt, worst, opdata))
    return ss, recs, n, results


def build_images(arms=(0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15), rep=0, prefix='e17'):
    for arm in arms:
        res = analyze(arm, prefix=prefix)
        if not res:
            continue
        ss, recs, n, results = res
        if not results:
            print(f'  arm{arm}: no reps, skip images')
            continue
        r, nxt, worst, opdata = results[rep]
        base = os.path.join(T, f'{prefix}-base-a{arm}.img')
        for tag, cut in (('worst', worst), ('best', nxt)):
            if cut <= 0:
                print(f'  arm{arm} {tag}: cut={cut} -> skip (empty)')
                continue
            out = os.path.join(T, f'{prefix}-crash-a{arm}-{tag}.img')
            with open(base, 'rb') as fb, open(out, 'wb') as fo:
                while True:
                    b = fb.read(16*1024*1024)
                    if not b:
                        break
                    fo.write(b)
            with open(out, 'r+b') as fo:
                for sec, nr, fl, d in recs[:cut]:
                    if nr and not (fl & F_DISCARD) and d:
                        fo.seek(sec * ss)
                        fo.write(d)
                fo.flush()
                os.fsync(fo.fileno())
            print(f'  built {os.path.basename(out)} cut={cut}/{n} '
                  f'({sum(1 for x in recs[:cut] if x[1])} writes)')


def report(arms=(0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15), prefix='e17'):
    for arm in arms:
        analyze(arm, prefix=prefix)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    prefix = sys.argv[2] if len(sys.argv) > 2 else 'e17'
    if mode == 'build':
        build_images(prefix=prefix)
    else:
        report(prefix=prefix)
