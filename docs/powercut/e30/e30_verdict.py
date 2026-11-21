#!/usr/bin/env python3
"""E30 verdict driver (school host). E30 = NT operation-level FILE_WRITE_THROUGH
shapes ported from run-e27.ps1 arms E/F/G into the E17 journal rig (arms 34-37).

Arms:
  34 nt-create-FWT : NtCreateFile(FILE_CREATE, FILE_NON_DIRECTORY_FILE|FWT) + write
  35 nt-rename-FWT : pre-create src, open with FWT, NtSetInformationFile(rename)
  36 nt-delete-FWT : pre-create src, open with FWT, disposition delete
  37 composite     : create tmp with FWT + write + FlushFileBuffers + rename via FWT

Usage: python e30_verdict.py report | build [arms]
"""
import os, re, sys

T = r'D:\yzd\leveldb-e2e-yhPf93BW'
sys.path.insert(0, T)
from e14_analyze import load_records

F_FLUSH, F_FUA, F_DISCARD = 1, 2, 4

ARM_NAMES = {
    34: 'NT CREATE (NtCreateFile FILE_CREATE|FILE_WRITE_THROUGH + write)',
    35: 'NT RENAME (FWT handle + NtSetInformationFile FileRenameInformation)',
    36: 'NT DELETE (FWT handle + FileDispositionInformation)',
    37: 'NT COMPOSITE (FWT create tmp + flush + FWT rename)',
}
NAME_TAG = {
    34: ['e30_e_r0.bin', 'e30_e_r1.bin', 'e30_e_r2.bin'],
    35: ['src_0', 'dst_0', 'src_1', 'dst_1', 'src_2', 'dst_2'],
    36: ['src_0', 'src_1', 'src_2'],
    37: ['tmp_0', 'final_0', 'tmp_1', 'final_1', 'tmp_2', 'final_2'],
}
BODY_TAG = {
    34: 'e30-e-r',
    35: 'e30-src-r',
    36: 'e30-src-r',
    37: 'e30-tmp-r',
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


def analyze(arm, reps=3, prefix='e30'):
    log = os.path.join(T, f'{prefix}-log-a{arm}.img')
    if not os.path.exists(log):
        print(f'== arm={arm}: no log, skip')
        return None
    ss, recs = load_records(log)
    n = len(recs)
    how = '?'
    go = []
    for r in range(reps):
        idx = find_tag(recs, f'E17GO arm={arm} rep={r}')
        go.append(idx[-1] if idx else None)
        if idx:
            m = re.search(rb'how=(\w)', recs[idx[0]][3])
            if m:
                how = m.group(1).decode()
    done_idx = find_tag(recs, f'E17DONE arm={arm}')
    done = done_idx[-1] if done_idx else None
    note = ''
    if done is not None:
        mm = re.search(rb'errs=([-\d,]+)( n\d=[^,]*(?:, n\d=.*)?)?',
                       recs[done][3])
        note = (mm.group(1).decode() + (mm.group(2).decode() if mm.group(2) else '')) if mm else ''
    print(f'== arm={arm} ({ARM_NAMES.get(arm, "?")}) how={how} records={n} '
          f'GO_data={go} DONE_data={done} errs[{note}]')

    for r in range(reps):
        nxt = go[r + 1] if r + 1 < reps and go[r + 1] is not None else done
        if nxt is None:
            print(f'  rep{r}: NO next marker - skip')
            continue
        worst = barrier_end(recs, nxt)
        tags = NAME_TAG[arm][r*2:r*2+2] if arm in (35, 37) else [NAME_TAG[arm][r]]
        for tag in tags:
            hits = find_tag(recs, tag)
            print(f'  rep{r}: {tag} barrier@={worst} '
                  f'name_first={hits[0] if hits else "NEVER"} all={hits[:6]}')
    return ss, recs, n


def build_images(arms=(34, 35, 36, 37), rep=0, prefix='e30'):
    for arm in arms:
        res = analyze(arm, prefix=prefix)
        if not res:
            continue
        ss, recs, n = res
        go = [None] * 3
        for r in range(3):
            idx = find_tag(recs, f'E17GO arm={arm} rep={r}')
            go[r] = idx[-1] if idx else None
        done_idx = find_tag(recs, f'E17DONE arm={arm}')
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
        arms = tuple(int(a) for a in sys.argv[2:]) or (34, 35, 36, 37)
        build_images(arms=arms)
    else:
        for arm in (34, 35, 36, 37):
            analyze(arm)