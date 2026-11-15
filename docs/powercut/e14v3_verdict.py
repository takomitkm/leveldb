#!/usr/bin/env python3
"""E14 v3 verdict driver (school host).

For each arm 0..5:
  1. parse the block log; locate marker DATA writes (unique, non-resident) and
     op data writes.
  2. cuts:
       nxt(r)      = GO(r+1) data idx, or DONE idx for the last rep
       worst(r)    = barrier_end(nxt(r))  -- guaranteed-durable prefix at op
                                             return (last FLUSH/FUA barrier)
       best(r)     = nxt(r)               -- everything the device received
  3. build crash images (rep 0 only, plus rep 2 worst for the last-rep sanity)
       e14v3-crash-a<arm>-worst.img / -best.img
  4. print log-level verdict summary.

Usage: python e14v3_verdict.py build   (images for all arms)
       python e14v3_verdict.py report  (log-level summary only)
"""
import os, re, sys

T = r'<TASKDIR>'
sys.path.insert(0, T)
from e14_analyze import load_records

F_FLUSH, F_FUA, F_DISCARD = 1, 2, 4


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
    idx = [i for i, (s, nr, fl, d) in enumerate(recs) if d and tag.encode() in d]
    return idx


def analyze(arm, reps=3, prefix='e14v3'):
    log = os.path.join(T, f'{prefix}-log-a{arm}.img')
    ss, recs = load_records(log)
    n = len(recs)
    how = '?'
    go = []
    for r in range(reps):
        idx = find_unique(recs, f'E14GO arm={arm} rep={r}')
        go.append(idx[-1] if idx else None)
        if idx:
            m = re.search(rb'how=(\w)', recs[idx[0]][3])
            if m:
                how = m.group(1).decode()
    done = find_unique(recs, f'E14DONE arm={arm}')
    done = done[-1] if done else None
    print(f'== arm={arm} how={how} records={n} GO_data={go} DONE_data={done}')

    results = []
    for r in range(reps):
        nxt = go[r+1] if r+1 < reps and go[r+1] is not None else done
        if nxt is None:
            print(f'  rep{r}: NO next marker — skip'); continue
        worst = barrier_end(recs, nxt)
        if arm in (3, 4):
            opdata = find_unique(recs, f'e14-rn{arm}-r{r}')
        else:
            opdata = find_unique(recs, f'e14-a{arm}-r{r}')
        print(f'  rep{r}: nxt={nxt} worst_prefix={worst} op_data_writes={opdata}')
        results.append((r, nxt, worst, opdata))
    return ss, recs, n, results


def build_images(arms=(0,1,2,3,4,5), rep=0, prefix='e14v3'):
    for arm in arms:
        ss, recs, n, results = analyze(arm, prefix=prefix)
        r, nxt, worst, opdata = results[rep]
        base = os.path.join(T, f'{prefix}-datadisk-a{arm}.img')
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


def report(arms=(0,1,2,3,4,5), prefix='e14v3'):
    for arm in arms:
        analyze(arm, prefix=prefix)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    prefix = sys.argv[2] if len(sys.argv) > 2 else 'e14v3'
    if mode == 'build':
        build_images(prefix=prefix)
    else:
        report(prefix=prefix)