"""Run a gate and turn its output into a GateResult.

Gates always run from a pinned base checkout (cwd) with the PR's decomp files copied in by the
caller; a PR can never change the gate's own scripts (those paths are protected).
"""
import hashlib
import re
import subprocess

from ..decider import GateResult

TAIL = 4000


def run(gate, cwd, head_sha, pr_number=0, env=None):
    cmd = gate.run.format(pr=pr_number, sha=head_sha)
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True,
                           text=True, errors='replace', timeout=gate.timeout)
        out, code = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or '') if isinstance(e.stdout, str) else ''
        return GateResult(gate.name, 'error', f'timed out after {gate.timeout}s', head_sha, _sha(out))
    return parse(gate, code, out, head_sha)


def parse(gate, code, out, head_sha):
    if gate.kind == 'rexcheck-gate':
        return _rexcheck(gate, code, out, head_sha)
    status = 'pass' if code == 0 else 'fail'
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return GateResult(gate.name, status, lines[-1][:200] if lines else f'exit {code}', head_sha, _sha(out),
                      {'tail': out[-TAIL:]})


# decompathon tools/rexcheck/gate.py output
MUT = re.compile(r'^mutants: (\d+)/(\d+) caught', re.M)
ZL = re.compile(r'^zero-loss vs \S+: (\d+) WORSE, (\d+) better/new', re.M)
WORSE = re.compile(r'^\s*WORSE 0x([0-9A-F]{8}): (\S+) -> (\S+)', re.M)
BETTER = re.compile(r'^\s*\+\s+0x([0-9A-F]{8}): (\S+) -> (\S+)', re.M)
VERDICT = re.compile(r'^GATE (PASS|FAIL)', re.M)


JUDGE = re.compile(r'^JUDGE (FAIL|ERROR) (.+)$', re.M)


def _rexcheck(gate, code, out, head_sha):
    j = JUDGE.search(out)
    if j:   # the judge stopped before gate.py: FAIL = the PR's code (no compile, game died), ERROR = harness
        return GateResult(gate.name, 'fail' if j.group(1) == 'FAIL' else 'error', j.group(2)[:200], head_sha,
                          _sha(out), {'tail': out[-TAIL:]})
    m, z, v = MUT.search(out), ZL.search(out), VERDICT.search(out)
    if not (m and z and v):
        # the harness itself broke: not the contributor's fault, a human looks
        return GateResult(gate.name, 'error', f'unreadable rexcheck output (exit {code})', head_sha, _sha(out),
                          {'tail': out[-TAIL:]})
    details = {
        'mutants_caught': int(m.group(1)), 'mutants': int(m.group(2)),
        'worse': [{'addr': a, 'was': b, 'now': c} for a, b, c in WORSE.findall(out)],
        'better': [{'addr': a, 'was': b, 'now': c} for a, b, c in BETTER.findall(out)],
    }
    passed = v.group(1) == 'PASS' and code == 0
    if not passed and details['mutants_caught'] < details['mutants']:
        # a blind checker is a harness problem, never a contributor failure
        return GateResult(gate.name, 'error', f'mutant gate {m.group(1)}/{m.group(2)}: checker is blind somewhere',
                          head_sha, _sha(out), details)
    summary = f'{z.group(1)} lost, {z.group(2)} new/better'
    return GateResult(gate.name, 'pass' if passed else 'fail', summary, head_sha, _sha(out), details)


def _sha(s):
    return hashlib.sha256(s.encode('utf-8', 'replace')).hexdigest()
