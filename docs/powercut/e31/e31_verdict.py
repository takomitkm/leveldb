#!/usr/bin/env python3
"""E31 verdict driver (school host). E31 = rightkit public-API single ops
via wrapper (arms 20-23). Derived from e29_verdict.py.

Arms (E28 public API, launched by e29pe wrapper):
  20 create-dir: StateDir::create(X:\\e14\\rk_<rep>)  -- mkdir via NtCreateFile FWT
  21 rename:     parent/src -> rename_exclusive(parent/src, rk_<rep>)
  22 delete:     create rk_<rep> + write + state.remove(rk_<rep>)
  23 write:      state.write(rk_<rep>, [0x57;2048])   -- atomic write

The operation target is the DIRECTORY rk_<rep> (or the file inside it).
Verdict: is the namespace change (dir/file present) durable at the worst
barrier cut? We look for the target NAME in the journal (UTF-16LE $FILE_NAME /
$I30) and, more decisively, mount the crash image and ls the e14 dir.

Usage: python e31_verdict.py report | build [arms]
"""
import os, re, sys

T = r'D:\yzd\leveldb-e2e-yhPf93BW'
sys.path.insert(0, T)
from e14_analyze import load_records

F_FLUSH, F_FUA, F_DISCARD = 1, 2, 4

ARM_NAMES = {
    20: 'RIGHTKIT CREATE-DIR (StateDir::create rk_N)',
    21: 'RIGHTKIT RENAME (rename_exclusive src->rk_N)',
    22: 'RIGHTKIT DELETE (create rk_N + write + remove)',
    23: 'RIGHTKIT WRITE (state.write rk_N, atomic)',
}
# The target directory/file NAME for each arm (per rep).
NAME_TAG = {
    20: ['rk_0', 'rk_1', 'rk_2'],
    21: ['rk_0', 'rk_1', 'rk_2'],   # dst = rk_N; src also in parent
    22: ['rk_0', 'rk_1', 'rk_2'],   # created then removed
    23: ['rk_0', 'rk_1', 'rk_2'],
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


def find_tag(recs, tag):
    a = tag.encode('utf-8')
    w = tag.encode('utf-16-le')
    hits = []
    for i, (s, nr, fl, d) in enumerate(recs):
        if not d:
            continue
        if a in d or w in d:
            hits.append(i)
    return hits


def analyze(arm, reps=3, prefix='e31'):
    log = os.path.join(T, f'{prefix}-log-a{arm}.img')
    if not os.path.exists(log):
        print(f'== arm={arm}: no log, skip')
        return None
    ss, recs = load_records(log)
    n = len(recs)
    how = '?'
    go = []
    for r in range(reps):
        idx = find_tag(recs, f'E29GO arm={arm} rep={r}')
        go.append(idx[-1] if idx else None)
    done_idx = find_tag(recs, f'E29DONE arm={arm}')
    done = done_idx[-1] if done_idx else None
    note = ''
    if done is not None:
        mm = re.search(rb'errs=([-\d,]+)( n\d=[^,]*(?:, n\d=.*)?)?',
                       recs[done][3])
        note = (mm.group(1).decode() + (mm.group(2).decode() if mm.group(2) else '')) if mm else ''
    print(f'== arm={arm} ({ARM_NAMES.get(arm, "?")}) records={n} '
          f'GO_data={go} DONE_data={done} errs[{note}]')

    for r in range(reps):
        nxt = go[r + 1] if r + 1 < reps and go[r + 1] is not None else done
        if nxt is None:
            print(f'  rep{r}: NO next marker — skip')
            continue
        worst = barrier_end(recs, nxt)
        tag = NAME_TAG[arm][r] if r < len(NAME_TAG[arm]) else f'rk_{r}'
        hits = find_tag(recs, tag)
        print(f'  rep{r}: target={tag} barrier@={worst} name_first={hits[0] if hits else "NEVER"} '
              f'name_all={hits[:8]}')
    return ss, recs, n


def build_images(arms=(20, 21, 22, 23), rep=0, prefix='e31'):
    for arm in arms:
        res = analyze(arm, prefix=prefix)
        if not res:
            continue
        ss, recs, n = res
        go = [None] * 3
        for r in range(3):
            idx = find_tag(recs, f'E29GO arm={arm} rep={r}')
            go[r] = idx[-1] if idx else None
        done_idx = find_tag(recs, f'E29DONE arm={arm}')
        done = done_idx[-1] if done_idx else None
        nxt = go[rep + 1] if rep + 1 < 3 and go[rep + 1] is not None else done
        if nxt is None:
            print(f'  arm{arm}: no cut point, skip images')
            continue
        worst = barrier_end(recs, nxt)
        base = os.path.join(T, f'{prefix}-base-a{arm}.img')
        for tag, cut in (('worst', worst), ('best', nxt)):
            if cut <= 0:
                print(f'  arm{arm} {tag}: cut={cut} -> skip (empty)')
                continue
            out = os.path.join(T, f'{prefix}-crash-a{arm}-{tag}.img')
            with open(base, 'rb') as fb, open(out, 'wb') as fo:
                while True:
                    b = fb.read(16 * 1024 * 1024)
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


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if mode == 'build':
        arms = tuple(int(a) for a in sys.argv[2:]) or (20, 21, 22, 23)
        build_images(arms=arms)
    else:
        for arm in (20, 21, 22, 23):
            analyze(arm)
