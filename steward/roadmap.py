"""Which functions to suggest next. Deterministic; the planner only writes prose around it.

Snowboard Kids 2 lesson: picking easiest-first stalls (~58%); picking functions that look like
ones already done works better. Without retail bytes (public side) "looks like" is approximated
by address proximity to done functions (same translation unit) plus shape.

difficulty = size + callees + impure/float/vector penalties     (lower is easier)
score      = difficulty * (1 - proximity bonus)                 (lower is suggested first)
"""
import bisect
import csv
import math
import os


def load_table(cfg):
    with open(os.path.join(cfg.root, cfg.function_table), newline='') as f:
        return list(csv.DictReader(f))


def difficulty(r):
    size = int(r.get('size') or 0)
    d = math.log2(max(size, 4)) + 0.6 * int(r.get('callees') or 0)
    if r.get('closure') == 'impure':
        d += 2
    if int(r.get('float') or 0):
        d += 1
    if int(r.get('vector') or 0):
        d += 3
    if int(r.get('indirect') or 0):
        d += 1
    return d


def suggest(rows, done, claimed, n=20, window=0x2000):
    """rows: function table; done: set of addrs; claimed: callable(addr, size)->bool."""
    done_sorted = sorted(done)
    out = []
    for r in rows:
        a = int(r['addr'], 16)
        size = int(r.get('size') or 0)
        if a in done or claimed(a, size) or size < 8:
            continue
        i = bisect.bisect_left(done_sorted, a)
        near = min([abs(a - done_sorted[j]) for j in (i - 1, i) if 0 <= j < len(done_sorted)] or [window])
        bonus = 0.5 * max(0.0, 1 - near / window)
        out.append((difficulty(r) * (1 - bonus), a, size, r.get('name', ''), near < window))
    out.sort()
    return out[:n]


def done_from_verdicts(cfg, name='verdicts.csv'):
    p = os.path.join(cfg.root, os.path.dirname(cfg.function_table), name)
    if not os.path.exists(p):
        return set()
    with open(p, newline='') as f:
        return {int(r['addr'], 16) for r in csv.DictReader(f) if r.get('verdict') in ('behaviour', 'byte')}


def _size(r):
    return int(r.get('size') or '0', 0)   # functions.csv writes sizes in hex (0xCC); plain decimal still works


def stats(rows, done):
    total = sum(_size(r) for r in rows)
    sizes = {int(r['addr'], 16): _size(r) for r in rows}
    got = sum(sizes.get(a, 0) for a in done)
    return {'functions': len(rows), 'bytes': total, 'done_functions': len(done), 'done_bytes': got,
            'percent': 100.0 * got / total if total else 0.0}
