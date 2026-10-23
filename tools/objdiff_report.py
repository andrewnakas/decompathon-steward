#!/usr/bin/env python3
"""Write an objdiff-format progress report (report.json, report version 2) without a ROM.

Progress trackers (ByteMatch, decompilati.onl, formerly decomp.dev) read objdiff's
`report.json`. objdiff-cli builds that file by diffing target objects cut from the ROM
against compiled objects, which needs the ROM. Projects that prove matches elsewhere
(a judge machine, a gate script) and only merge proven functions can describe the same
facts from two ROM-free inputs:

  * a functions table:  addr,size,name[,...]   (header row optional; hex or decimal)
  * the set of matched function names          (a directory of <name>.c files and/or a list)

Every matched function counts as a 100% match, every other one as 0%. Data is not
tracked (total_data = 0). No unit is marked complete ("linked"), because the binary is
not rebuilt from source alone.

Units: by default the whole table is one unit (`--unit main`). `--group-bytes N` splits
it into address-range units of about N bytes each (a map for the trackers; these are
address ranges, not real translation units). `--category ID[:NAME]` adds a progress
category holding every unit.

Examples:
  python tools/objdiff_report.py --functions config/functions.csv \\
      --matched-dir src/funcs --version us_v1.1
  python tools/objdiff_report.py --functions funcs.csv --matched-list matched.txt \\
      -o report.json --group-bytes 0x10000 --category game:Game
Prints a one-line summary; the JSON goes to -o (default build/<version>/report.json).
"""
import argparse
import csv
import glob
import json
import os
import sys

REPORT_VERSION = 2  # objdiff-core REPORT_VERSION (bindings/report.rs)


def parse_int(s):
    s = s.strip()
    return int(s, 16) if s.lower().startswith('0x') else int(s)


def read_functions(path):
    """Rows of (addr, size, name). Accepts a header row with addr/size/name columns, or
    a headerless addr,size,name table. Duplicate names keep the first row."""
    with open(path, newline='') as f:
        rows = [r for r in csv.reader(f) if r and not r[0].lstrip().startswith('#')]
    if not rows:
        sys.exit(f'{path}: no rows')
    cols = {'addr': 0, 'size': 1, 'name': 2}
    head = [c.strip().lower() for c in rows[0]]
    if 'name' in head and 'size' in head:
        cols = {k: head.index(k) for k in ('size', 'name')}
        cols['addr'] = next(head.index(k) for k in ('addr', 'address', 'vram') if k in head)
        rows = rows[1:]
    out, seen = [], set()
    for r in rows:
        addr, size, name = parse_int(r[cols['addr']]), parse_int(r[cols['size']]), r[cols['name']].strip()
        if size <= 0 or name in seen:
            continue
        seen.add(name)
        out.append((addr, size, name))
    out.sort()
    return out


def read_matched(dirs, pattern, lists):
    names = set()
    for d in dirs or []:
        for p in glob.glob(os.path.join(d, pattern)):
            names.add(os.path.splitext(os.path.basename(p))[0])
    for path in lists or []:
        with open(path) as f:
            names.update(l.strip() for l in f if l.strip() and not l.startswith('#'))
    return names


def pct(part, total):
    return 100.0 * part / total if total else 0.0


def measures(funcs, matched, units=1):
    """objdiff Measures. 64-bit counts are strings, as protobuf JSON (pbjson) writes them."""
    total = sum(s for _, s, _ in funcs)
    done = sum(s for _, s, n in funcs if n in matched)
    nf, mf = len(funcs), sum(1 for f in funcs if f[2] in matched)
    return {
        'fuzzy_match_percent': pct(done, total),
        'total_code': str(total),
        'matched_code': str(done),
        'matched_code_percent': pct(done, total),
        'total_data': '0',
        'matched_data': '0',
        'matched_data_percent': 0.0,
        'total_functions': nf,
        'matched_functions': mf,
        'matched_functions_percent': pct(mf, nf),
        'complete_code': '0',
        'complete_code_percent': 0.0,
        'complete_data': '0',
        'complete_data_percent': 0.0,
        'total_units': units,
        'complete_units': 0,
    }


def make_unit(name, funcs, matched, section, categories, source_path):
    base = funcs[0][0]
    m = measures(funcs, matched)
    meta = {'complete': False, 'progress_categories': categories}
    if source_path:
        meta['source_path'] = source_path
    return {
        'name': name,
        'measures': m,
        'sections': [{
            'name': section,
            'size': m['total_code'],
            'fuzzy_match_percent': m['fuzzy_match_percent'],
            'metadata': {'virtual_address': str(base)},
        }],
        'functions': [{
            'name': n,
            'size': str(s),
            'fuzzy_match_percent': 100.0 if n in matched else 0.0,
            'metadata': {'virtual_address': str(a)},
            'address': str(a - base),
        } for a, s, n in funcs],
        'metadata': meta,
    }


def group(funcs, nbytes):
    if nbytes <= 0:
        return [funcs]
    out, cur, start = [], [], None
    for f in funcs:
        if cur and f[0] - start >= nbytes:
            out.append(cur)
            cur = []
        if not cur:
            start = f[0]
        cur.append(f)
    if cur:
        out.append(cur)
    return out


def build(funcs, matched, unit='main', section='.text', group_bytes=0, category=None, source_path=None):
    cats = []
    cat_ids = []
    if category:
        cid, _, cname = category.partition(':')
        cat_ids = [cid]
    units = []
    chunks = group(funcs, group_bytes)
    for chunk in chunks:
        name = unit if len(chunks) == 1 else f'{unit}/{chunk[0][0]:08X}-{chunk[-1][0] + chunk[-1][1]:08X}'
        units.append(make_unit(name, chunk, matched, section, cat_ids, source_path))
    if category:
        cats = [{'id': cat_ids[0], 'name': cname or cat_ids[0], 'measures': measures(funcs, matched, len(units))}]
    return {
        'measures': measures(funcs, matched, len(units)),
        'units': units,
        'version': REPORT_VERSION,
        'categories': cats,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--functions', required=True, help='CSV: addr,size,name[,...]')
    ap.add_argument('--matched-dir', action='append', help='directory of matched <name>.<ext> files (repeatable)')
    ap.add_argument('--matched-glob', default='*.c', help='file pattern inside --matched-dir (default *.c)')
    ap.add_argument('--matched-list', action='append', help='text file, one matched name per line (repeatable)')
    ap.add_argument('--version', default='default', help='game version, only used for the default output path')
    ap.add_argument('-o', '--output', help='output path (default build/<version>/report.json)')
    ap.add_argument('--unit', default='main', help='unit name, or name prefix with --group-bytes (default main)')
    ap.add_argument('--section', default='.text', help='section name for the code (default .text)')
    ap.add_argument('--group-bytes', type=parse_int, default=0, help='split into address-range units of ~N bytes')
    ap.add_argument('--category', help='progress category ID[:NAME] holding every unit')
    ap.add_argument('--source-path', help='source_path metadata for the unit(s), e.g. src/funcs')
    ap.add_argument('--compact', action='store_true', help='no indentation')
    a = ap.parse_args()
    if not a.matched_dir and not a.matched_list:
        ap.error('give --matched-dir and/or --matched-list')

    funcs = read_functions(a.functions)
    matched = read_matched(a.matched_dir, a.matched_glob, a.matched_list)
    unknown = sorted(matched - {n for _, _, n in funcs})
    matched -= set(unknown)
    report = build(funcs, matched, a.unit, a.section, a.group_bytes, a.category, a.source_path)

    out = a.output or os.path.join('build', a.version, 'report.json')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', newline='\n') as f:
        json.dump(report, f, indent=None if a.compact else 2)
        f.write('\n')
    m = report['measures']
    print(f"{out}: {m['matched_functions']}/{m['total_functions']} functions, "
          f"{m['matched_code']}/{m['total_code']} bytes ({m['matched_code_percent']:.3f}%), "
          f"{len(report['units'])} unit(s)"
          + (f"; ignored {len(unknown)} matched name(s) not in the table, e.g. {unknown[0]}" if unknown else ''))


if __name__ == '__main__':
    main()
