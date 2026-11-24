"""E33.1 acceptance-criteria audit against a downloaded run artifact.

Usage:
    python e33_audit.py --artifacts <e33-artifacts-...> [--repo <repo root>]

Checks the nine E33.1 acceptance criteria against the artifact itself (not
against numbers quoted in prose) and prints a PASS/FAIL table.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

_ap = argparse.ArgumentParser(description="E33.1 acceptance-criteria audit")
_ap.add_argument("--artifacts", required=True,
                 help="the e33-artifacts-* directory of an E33.1 run")
_ap.add_argument("--repo", default=None,
                 help="repository root (defaults to the checkout this file "
                      "lives in)")
ARGS = _ap.parse_args()

WT = ARGS.repo or os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
BASE = ARGS.artifacts

sys.path.insert(0, os.path.join(WT, "docs", "powercut", "e33"))
import e33_integrate as integ  # noqa: E402

results = []


def check(name, ok, detail):
    results.append((name, bool(ok), detail))


# 1. The fixed decoder identifies the exact test names in every arm.
rows = list(csv.DictReader(open(os.path.join(BASE, 'decoded-index-records.csv'),
                              encoding='utf-8')))
arms = sorted(set(r['arm'] for r in rows))
bad = [r for r in rows if not r['filename'].startswith('e33_' + r['arm'] + '_')]
per_arm = {a: sum(1 for r in rows if r['arm'] == a) for a in arms}
check('1 decoder finds exact test names in every arm',
      len(arms) == 15 and not bad and min(per_arm.values()) >= 100,
      'arms=%d, min records/arm=%d, foreign-name rows=%d'
      % (len(arms), min(per_arm.values()), len(bad)))

# 2. Target-page writes computed by LSN-to-physical-offset matching.
tw = list(csv.DictReader(open(os.path.join(BASE, 'target-page-writes.csv'),
                             encoding='utf-8')))
need = {'arm', 'operation', 'filename', 'lsn', 'transaction_id', 'rcrd_page',
        'stream_offset', 'physical_byte_offset', 'write_timestamp', 'write_size'}
check('2 target-page writes use the required columns',
      need.issubset(set(tw[0].keys())) if tw else False,
      'columns present=%s' % sorted(need & set(tw[0].keys()) if tw else []))
check('2b every target-page write carries a physical offset and a page',
      all(r['physical_byte_offset'] and r['rcrd_page'] for r in tw) if tw else False,
      '%d rows' % len(tw))

# 3. Unrelated $LogFile writes excluded.
vi = json.load(open(os.path.join(BASE, 'volume-info.json'), encoding='utf-8'))
etw = json.load(open(os.path.join(BASE, 'etw-summary.json'), encoding='utf-8'))
total_log_writes = 0
for a in etw['arms']:
    arm = a['arm']
    ext = json.load(open(os.path.join(BASE, 'logfile',
                                      'arm-%s-extract.json' % arm),
                         encoding='utf-8'))
    ranges = integ.logfile_ranges(ext)
    total_log_writes += sum(
        1 for w in a['writes']
        if integ.device_to_stream(w['offset'], vi['partition_offset'], ranges))
foreign = [r for r in tw
           if not r['filename'].startswith('e33_' + r['arm'] + '_')]
check('3 unrelated $LogFile writes excluded',
      not foreign and total_log_writes > 0,
      '%d $LogFile writes total, %d target rows, %d foreign-name rows'
      % (total_log_writes, len(tw), len(foreign)))

# 4. Background lazy-writer activity separated.
l3 = json.load(open(os.path.join(BASE, 'l3-attribution.json'),
                    encoding='utf-8'))['calls']
ev = json.load(open(os.path.join(BASE, 'E33-evidence.json'), encoding='utf-8'))
bg_arms = [a for a in ('A', 'J', 'L')
            if ev['level_counts'][a]['L3'] == 0]
check('4 background activity separated (no-flush arms reach no L3)',
      len(bg_arms) == 3 and ev['level_counts']['A']['L1'] > 0,
      'arms with zero L3: %s; arm A L1 count=%d'
      % (bg_arms, ev['level_counts']['A']['L1']))

# 5. Barriers correlated to the actual candidate API call.
l3calls = [c for c in l3 if c['level'] == 'L3']
thread_ok = all(cv.get('thread_match')
                for c in l3calls for cv in c['covered_writes'])
api_ok = all(c['api_ok'] for c in l3calls)
order_ok = all(c['op_start_ft'] <= cv['write_t'] <= cv['barrier_start']
               <= cv['barrier_end'] <= c['flush_end_ft']
               for c in l3calls for cv in c['covered_writes'])
check('5 barriers correlated to the candidate call (order + thread + API ok)',
      thread_ok and api_ok and order_ok and len(l3calls) > 0,
      'L3 calls=%d, thread match=%s, api ok=%s, causal order=%s'
      % (len(l3calls), thread_ok, api_ok, order_ok))

# 6. Evidence regenerable from the uploaded artifact.
tmp = tempfile.mkdtemp()
for name in os.listdir(BASE):
    src = os.path.join(BASE, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(tmp, name))
    elif name in ('logfile', 'etw'):
        shutil.copytree(src, os.path.join(tmp, name))
proc = subprocess.run(
    [sys.executable, os.path.join(WT, 'docs', 'powercut', 'e33',
                                 'e33_integrate.py'), '--artifacts', tmp],
    capture_output=True, text=True)
same = True
for name in ('E33-evidence.json', 'l3-attribution.json',
             'target-page-writes.csv'):
    a = open(os.path.join(tmp, name), encoding='utf-8').read()
    b = open(os.path.join(BASE, name), encoding='utf-8').read()
    same = same and a == b
check('6 evidence regenerates byte-identically from the artifact',
      proc.returncode == 0 and same,
      'integrator rc=%d, outputs identical=%s' % (proc.returncode, same))

# 7. Same-layout parent-directory vs $I30 stream comparisons exist.
fr = json.load(open(os.path.join(BASE, 'fixture-residency.json'),
                     encoding='utf-8'))
large_nonres = any(v.get('file_name') == 'large' and v.get('i30_nonresident')
                   for v in fr.values())
small_res = any(v.get('file_name') == 'small' and not v.get('i30_nonresident')
                 for v in fr.values())
check('7 same-layout comparison exists (C/D/E and F/G/H/I on the large dir)',
      large_nonres and small_res,
      'large non-resident=%s, small resident=%s' % (large_nonres, small_res))

# 8. The latest workflow succeeded.
build_path = os.path.join(BASE, 'e33-build-info.json')
if not os.path.exists(build_path):
    # older runs uploaded it next to the artifacts directory
    cand = os.path.join(os.path.dirname(BASE), 'e33-build-info.json')
    build_path = cand if os.path.exists(cand) else build_path
if os.path.exists(build_path):
    build = json.load(open(build_path, encoding='utf-8'))
check('8 the run records the exact code SHA that produced it',
      bool(build.get('commit_sha')) and bool(build.get('run_id')),
      'commit_sha=%s run_id=%s'
      % (build.get('commit_sha', '')[:12], build.get('run_id')))

# 9. No unverified durability claims: no L4/L5 asserted anywhere.
res = open(os.path.join(WT, 'docs', 'powercut', 'E33.1-RESULTS.md'),
           encoding='utf-8').read()
l4_rows = [ln for ln in res.splitlines()
           if ln.startswith('| ') and '| **L4' in ln]
l5_ok = 'L5' in res and 'untested' in res.lower()
check('9 no unverified durability claim (no L4/L5 asserted)',
      not l4_rows and l5_ok,
      'rows asserting L4=%d, L5 marked untested=%s' % (len(l4_rows), l5_ok))

print('=' * 78)
for name, ok, detail in results:
    print('%-4s %-62s %s' % ('PASS' if ok else 'FAIL', name, detail))
print('=' * 78)
failed = [n for n, ok, _ in results if not ok]
print('%d/%d criteria pass' % (len(results) - len(failed), len(results)))
if failed:
    print('FAILED:', failed)
    sys.exit(1)
