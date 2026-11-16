#!/usr/bin/env python3
"""E17 MFT-zone analysis: writes to the MFT/index metadata area (offset
< 8 MiB) per arm, split by position relative to the rep windows (between
GO(r) and GO(r+1)/DONE markers) vs the park window (after DONE).  Tells us
whether the single-FlushDir arms (1/2/3/4/5/7/8) produced ANY in-window
metadata device writes, versus the double-flush pre-state arms (6/10)."""
import os, sys

T = r'<TASKDIR>'
sys.path.insert(0, T)
from e14_analyze import load_records

ARMS = tuple(int(x) for x in (__import__('os').environ.get('ARMS', '0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15').split(',')))


def find(recs, tag):
    return [i for i, (s, nr, fl, d) in enumerate(recs) if d and tag.encode() in d]


for arm in ARMS:
    path = os.path.join(T, __import__('os').environ.get('PREFIX', 'e17') + f'-log-a{arm}.img')
    if not os.path.exists(path):
        print(f'arm={arm}: no log')
        continue
    ss, recs = load_records(path)
    n = len(recs)
    go = []
    for r in range(3):
        idx = find(recs, f'E17GO arm={arm} rep={r}')
        go.append(idx[-1] if idx else None)
    done = find(recs, f'E17DONE arm={arm}')
    done_i = done[-1] if done else None
    bounds = []
    for r in range(3):
        lo = go[r]
        hi = go[r + 1] if r + 1 < 3 and go[r + 1] is not None else done_i
        bounds.append((lo, hi))
    win_w = [0] * 3
    park_w = 0
    pre_w = 0
    mft_samples = {r: [] for r in range(3)}
    for i, (s, nr, fl, d) in enumerate(recs):
        if not nr or s * ss >= 8 * 1024 * 1024:
            continue
        placed = False
        for r, (lo, hi) in enumerate(bounds):
            if lo is not None and hi is not None and lo < i < hi:
                win_w[r] += 1
                mft_samples[r].append((i, s * ss, nr * ss, 'F' if fl & 1 else '-'))
                placed = True
                break
        if not placed:
            if done_i is not None and i > done_i:
                park_w += 1
            else:
                pre_w += 1
    print(f'arm={arm:2d}: recs={n} MFT-in-window(rep0/1/2)={win_w} '
          f'park={park_w} pre={pre_w}')
    for r in range(3):
        if mft_samples[r]:
            print(f'        rep{r} sample: {mft_samples[r][:8]}')
