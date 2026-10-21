#!/usr/bin/env python3
"""Generate the private Steward test repo: a toy decomp project with a real behaviour oracle.

Ten "retail" functions exist only as Python reference code HERE (this file never goes into the
test repo). The repo gets their addresses/sizes, test vectors, three already-decompiled C files,
mutants, a scan gate and an oracle gate that compiles the C with gcc and compares against the
vectors, printing the same report format as Decompathon's rexcheck gate.py.

  python make_testrepo.py <out_dir>
"""
import json
import os
import random
import sys
import textwrap

M = 0xFFFFFFFF


def add_sat(a, b): return min(a + b, M)
def rotl(a, b): s = b & 31; return ((a << s) | (a >> ((32 - s) & 31))) & M if s else a
def popcount(a, b): return bin(a).count('1')
def isqrt(a, b):
    x = int(a ** 0.5)
    while x * x > a: x -= 1
    while (x + 1) * (x + 1) <= a: x += 1
    return x
def gcd(a, b):
    while b: a, b = b, a % b
    return a
def bitrev(a, b): return int(f'{a:032b}'[::-1], 2)
def fnv1a(a, b):
    h = b ^ 0x811C9DC5
    for i in range(4):
        h = ((h ^ ((a >> (8 * i)) & 0xFF)) * 0x01000193) & M
    return h
def crc8(a, b):
    c = b & 0xFF
    for i in range(4):
        c ^= (a >> (8 * i)) & 0xFF
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c
def collatz(a, b):
    n, s = a or 1, 0
    while n != 1 and s < 1000:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        s += 1
    return s
def xorshift(a, b):
    x = a or 1
    for _ in range((b & 7) + 1):
        x ^= (x << 13) & M; x ^= x >> 17; x ^= (x << 5) & M
    return x


FUNCS = [  # addr, size, ref, name (kept in the private generator only)
    (0x80001000, 24, add_sat, 'AddSaturate'), (0x80001040, 40, rotl, 'RotateLeft'),
    (0x80001080, 52, popcount, 'PopCount'), (0x800010C0, 96, fnv1a, 'Fnv1aWord'),
    (0x80001140, 60, gcd, 'Gcd'), (0x80001180, 140, crc8, 'Crc8Word'), (0x80001240, 88, isqrt, 'ISqrt'),
    (0x800012C0, 72, bitrev, 'BitReverse'), (0x80001340, 76, collatz, 'CollatzSteps'),
    (0x800013C0, 92, xorshift, 'XorShiftN'),
]

DONE_C = {
    0x80001000: 'uint32_t AddSaturate(uint32_t a, uint32_t b)\n{\n    uint32_t sum = a + b;\n    return sum < a ? 0xFFFFFFFFu : sum;\n}',
    0x80001040: 'uint32_t RotateLeft(uint32_t value, uint32_t amount)\n{\n    amount &= 31;\n    if (amount == 0)\n        return value;\n    return (value << amount) | (value >> (32 - amount));\n}',
    0x80001080: 'uint32_t PopCount(uint32_t value, uint32_t unused)\n{\n    uint32_t count = 0;\n    (void)unused;\n    while (value) {\n        value &= value - 1;\n        count++;\n    }\n    return count;\n}',
}
MUTANTS = {
    0x80001000: 'uint32_t AddSaturate(uint32_t a, uint32_t b) { return a + b; }',
    0x80001040: 'uint32_t RotateLeft(uint32_t v, uint32_t n) { return v << (n & 31); }',
    0x80001080: 'uint32_t PopCount(uint32_t v, uint32_t u) { (void)u; return v & 1; }',
}


def header(addr, author='andrewnakas', prov='human'):
    return f'// @addr 0x{addr:08X}\n// @author {author}\n// @provenance {prov}\n#include <stdint.h>\n\n'


def vectors(ref):
    rnd = random.Random(1234)
    edge = [0, 1, 2, 3, 31, 32, 0x7FFFFFFF, 0x80000000, M]
    pairs = [(a, b) for a in edge[:5] for b in edge[:3]] + [(rnd.getrandbits(32), rnd.getrandbits(32)) for _ in range(24)]
    pairs += [(rnd.randrange(1, 5000), rnd.randrange(1, 5000)) for _ in range(8)]
    return [[a, b, ref(a, b)] for a, b in pairs]


ORACLE = r'''#!/usr/bin/env python3
"""Behaviour oracle: compile each decomp function with gcc, run it on oracle/vectors.json.

Zero-loss compares the PR's decomp set with the base branch's (git show, nothing checked out),
and every mutant in tools/mutants must differ. Output format = Decompathon rexcheck gate.py.
  oracle.py [--base origin/main]
"""
import argparse, glob, json, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VEC = json.load(open(os.path.join(ROOT, 'oracle', 'vectors.json')))
FUNC = re.compile(r'^\s*uint32_t\s+(\w+)\s*\(', re.M)
MIN_EQUAL = 3


def run_c(src, addr):
    vec = VEC.get(addr)
    if not vec:
        return 'untestable', 0
    m = FUNC.search(src)
    if not m:
        return 'untestable', 0
    rows = ',\n'.join('{%du,%du,%du}' % tuple(v) for v in vec)
    main = (src + '\n#include <stdio.h>\nstatic const uint32_t V[][3] = {\n' + rows + '\n};\n'
            'int main(void){int eq=0,n=sizeof V/sizeof V[0];for(int i=0;i<n;i++){uint32_t r=%s(V[i][0],V[i][1]);'
            'printf("sample %%d %%s\\n",i,r==V[i][2]?"equal":"DIFFERS");eq+=r==V[i][2];}return 0;}\n') % m.group(1)
    with tempfile.TemporaryDirectory() as d:
        c, exe = os.path.join(d, 'f.c'), os.path.join(d, 'f')
        open(c, 'w').write(main)
        if subprocess.run(['gcc', '-O1', '-std=c11', '-w', '-o', exe, c], capture_output=True).returncode:
            return 'differs', 0
        try:
            out = subprocess.run([exe], capture_output=True, text=True, timeout=10).stdout
        except subprocess.TimeoutExpired:
            return 'differs', 0
    eq, dif = out.count(' equal'), out.count('DIFFERS')
    if dif:
        return 'differs', eq
    return ('behaviour' if eq >= MIN_EQUAL else 'weak'), eq


def files_at(ref):
    if ref is None:
        return {os.path.relpath(p, ROOT).replace(os.sep, '/'): open(p).read()
                for p in glob.glob(os.path.join(ROOT, 'decomp', '**', 'sub_*.c'), recursive=True)}
    ls = subprocess.run(['git', 'ls-tree', '-r', '--name-only', ref, '--', 'decomp'], cwd=ROOT, capture_output=True, text=True)
    out = {}
    for p in ls.stdout.split():
        if re.search(r'sub_[0-9A-F]{8}\.c$', p):
            out[p] = subprocess.run(['git', 'show', f'{ref}:{p}'], cwd=ROOT, capture_output=True, text=True).stdout
    return out


def verdicts(files):
    v = {}
    for p, src in files.items():
        addr = re.search(r'sub_([0-9A-F]{8})\.c$', p).group(1)
        v[addr] = run_c(src, addr)[0]
    return v


RANK = {'behaviour': 3, 'weak': 2, 'untestable': 1, 'differs': 0}
ap = argparse.ArgumentParser()
ap.add_argument('--base')
a = ap.parse_args()
ok = True
muts = sorted(glob.glob(os.path.join(ROOT, 'tools', 'mutants', 'm_*.c')))
caught = 0
for m in muts:
    addr = re.search(r'm_([0-9A-F]{8})\.c$', m).group(1)
    v, eq = run_c(open(m).read(), addr)
    caught += v == 'differs'
    print(f'  {"ok  " if v == "differs" else "MISS"} 0x{addr}  verdict={v}')
print(f'mutants: {caught}/{len(muts)} caught')
ok &= caught == len(muts)
base = verdicts(files_at(a.base)) if a.base else {}
now = verdicts(files_at(None))
worse = [(k, base[k], now.get(k, 'missing')) for k in sorted(base)
         if base[k] == 'behaviour' and RANK.get(now.get(k, 'untestable'), 1) < 3]
better = [(k, base.get(k, 'new'), v) for k, v in sorted(now.items()) if RANK[v] > RANK.get(base.get(k, ''), -1) and v == 'behaviour']
for k, v in sorted(now.items()):
    if v != 'behaviour':
        print(f'  note 0x{k}: {v}')
print(f'zero-loss vs {a.base or "nothing"}: {len(worse)} WORSE, {len(better)} better/new')
for k, b, n in worse:
    print(f'  WORSE 0x{k}: {b} -> {n}')
for k, b, n in better:
    print(f'  +     0x{k}: {b} -> {n}')
ok &= not worse
print('GATE', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
'''

SCAN = r'''#!/usr/bin/env python3
"""Rule scan for decomp files: header, one function per file, no asm, no reference-copying tricks."""
import glob, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = [(re.compile(r'\b(__asm__|asm\s*\(|__asm\b)'), 'inline asm'),
          (re.compile(r'vectors\.json|#\s*include\s*"'), 'reads the oracle or includes local files'),
          (re.compile(r'\b(system|popen|fopen|exec\w*)\s*\('), 'calls the OS')]
HEADER = re.compile(r'//\s*@(addr|author|provenance)\s+(\S+)')
fails = 0
files = sorted(glob.glob(os.path.join(ROOT, 'decomp', '**', '*.c'), recursive=True))
for p in files:
    src = open(p, encoding='utf-8', errors='replace').read()
    errs = []
    m = re.search(r'sub_([0-9A-F]{8})\.c$', p)
    hdr = dict(HEADER.findall(src))
    if not m:
        errs.append('file name must be sub_XXXXXXXX.c (upper-case hex)')
    for k in ('addr', 'author', 'provenance'):
        if k not in hdr:
            errs.append(f'missing // @{k}')
    if m and 'addr' in hdr and int(hdr['addr'], 16) != int(m.group(1), 16):
        errs.append('@addr does not match the file name')
    if hdr.get('provenance') not in (None, 'human', 'ai-assisted'):
        errs.append('@provenance must be human or ai-assisted')
    errs += [why for rx, why in BANNED if rx.search(src)]
    if errs:
        fails += 1
        print(f'FAIL {os.path.relpath(p, ROOT)}: ' + '; '.join(errs))
print(f'{len(files)} files scanned, {fails} failing')
sys.exit(1 if fails else 0)
'''

WORKFLOW = '''name: gates
on:
  pull_request:

permissions:
  contents: read

jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
      - name: scan
        run: |
          set +e
          python3 tools/scan.py > scan.log 2>&1; echo $? > scan.code
          cat scan.log
      - name: oracle
        run: |
          set +e
          python3 tools/oracle.py --base origin/${{ github.event.pull_request.base.ref }} > oracle.log 2>&1; echo $? > oracle.code
          cat oracle.log
      - name: meta
        run: |
          printf '{"pr": %s, "head_sha": "%s", "base_sha": "%s"}' "${{ github.event.pull_request.number }}" "${{ github.event.pull_request.head.sha }}" "${{ github.event.pull_request.base.sha }}" > meta.json
      - uses: actions/upload-artifact@v4
        with:
          name: steward-gates
          path: |
            meta.json
            scan.log
            scan.code
            oracle.log
            oracle.code
      - name: result
        run: test "$(cat scan.code)" = 0 && test "$(cat oracle.code)" = 0
'''

STEWARD_TOML = '''# Decompathon Steward config for the private test repo.
[project]
name = "steward-test"
repo = "andrewnakas/decompathon-steward-test"
default_branch = "main"
unit_paths = ["decomp/**"]
unit_regex = 'sub_([0-9A-F]{8})\\.c$'
function_table = "functions.csv"

[policy]
auto_merge = true
first_timer_needs_human = true
owner_rule_days = 7
max_units_per_pr = 20
maintainers = ["andrewnakas"]
protected_paths = ["tools/**", "oracle/**", "functions.csv", "RULES.md", "CODEOWNERS"]
required_checkboxes = ["No game files", "Provenance is honest"]

[[gates]]
name = "scan"
run = "artifact:scan"
where = "public"

[[gates]]
name = "oracle"
run = "artifact:oracle"
where = "public"
kind = "rexcheck-gate"

[models]
backend = "claude-code"
triage = "claude-haiku-5-5"
review = "claude-sonnet-5-5"
plan = "claude-opus-5-5"
mentor = "claude-sonnet-5-5"
'''

README = '''# Decompathon Steward test repo (private)

A toy decomp project for testing Decompathon Steward end to end. Ten "retail" functions are known only by
address, size and test vectors (`oracle/vectors.json`); decompile one into `decomp/<area>/sub_XXXXXXXX.c`.

- Claim first: comment `/claim 0x800010C0` on any issue (or `/claim` in Discord).
- Each file: `// @addr`, `// @author`, `// @provenance human|ai-assisted`, then `#include <stdint.h>` and one
  `uint32_t Name(uint32_t a, uint32_t b)` function.
- CI runs `tools/scan.py` and `tools/oracle.py` (gcc, compares every vector, zero-loss vs main, mutants must fail).
- Steward (a GitHub App + Claude) labels, explains, and merges when every gate is green. See RULES.md.
'''

RULES = '''# Rules (test repo)
1. One function per file, header with @addr/@author/@provenance (human or ai-assisted, both fine).
2. It must reproduce every oracle vector. No reading the vectors, no OS calls, no asm (scan gate).
3. Zero-loss: a PR may not break a function that already matches.
4. Claimed by someone else? Ask them. Merged by someone else? Steward asks them; 7 days of silence = go ahead.
5. Up to 20 functions per PR. Tooling/oracle/rules changes are decided by a maintainer.
6. Steward merges when the deterministic gates pass. Its AI notes only explain. Appeals: issue with `appeal`.
'''

TEMPLATE = '''## Functions

| Address | Name |
|---|---|
| 0x | |

## Checklist

- [ ] No game files, retail bytes or copied reference code
- [ ] Provenance is honest (human / ai-assisted)
'''


def main(out):
    os.makedirs(out, exist_ok=True)

    def w(rel, text):
        p = os.path.join(out, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    w('functions.csv', 'addr,size,name\n' + ''.join(f'0x{a:08X},{s},sub_{a:08X}\n' for a, s, _, _ in FUNCS))
    w('oracle/vectors.json', json.dumps({f'{a:08X}': vectors(ref) for a, _, ref, _ in FUNCS}, indent=0))
    for addr, body in DONE_C.items():
        w(f'decomp/core/sub_{addr:08X}.c', header(addr) + body + '\n')
    for addr, body in MUTANTS.items():
        w(f'tools/mutants/m_{addr:08X}.c', '#include <stdint.h>\n' + body + '\n')
    w('tools/oracle.py', ORACLE)
    w('tools/scan.py', SCAN)
    w('.github/workflows/gates.yml', WORKFLOW)
    w('.github/pull_request_template.md', TEMPLATE)
    w('steward.toml', STEWARD_TOML)
    w('README.md', README)
    w('RULES.md', RULES)
    w('CODEOWNERS', '* @andrewnakas\n')
    w('.gitignore', '__pycache__/\n*.log\n*.code\nmeta.json\n')
    print(f'wrote test repo to {out}: {len(FUNCS)} functions, {len(DONE_C)} decompiled, {len(MUTANTS)} mutants')


def reference_c(addr):
    """The correct C for a function (used by the scenario script to open 'good' PRs)."""
    return {
        0x800010C0: 'uint32_t Fnv1aWord(uint32_t word, uint32_t seed)\n{\n    uint32_t hash = seed ^ 0x811C9DC5u;\n    for (int i = 0; i < 4; i++) {\n        hash ^= (word >> (8 * i)) & 0xFF;\n        hash *= 0x01000193u;\n    }\n    return hash;\n}',
        0x80001140: 'uint32_t Gcd(uint32_t a, uint32_t b)\n{\n    while (b != 0) {\n        uint32_t t = a % b;\n        a = b;\n        b = t;\n    }\n    return a;\n}',
        0x800012C0: 'uint32_t BitReverse(uint32_t value, uint32_t unused)\n{\n    uint32_t out = 0;\n    (void)unused;\n    for (int i = 0; i < 32; i++) {\n        out = (out << 1) | (value & 1);\n        value >>= 1;\n    }\n    return out;\n}',
    }[addr]


if __name__ == '__main__':
    main(sys.argv[1])
