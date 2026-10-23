"""Run a gate and turn its output into a GateResult.

Gates always run from a pinned base checkout (cwd) with the PR's decomp files copied in by the
caller; a PR can never change the gate's own scripts (those paths are protected).
"""
import hashlib
import os
import re
import signal
import subprocess

from ..decider import GateResult
from .. import heartbeat

TAIL = 4000


def kill_tree(p):
    """Kill a gate's whole process tree. Killing only the shell leaves its children (compilers, linkers) running
    and holding the output pipes open, so the caller would wait on them forever."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True, timeout=60)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def run(gate, cwd, head_sha, pr_number=0, env=None):
    cmd = gate.run.format(pr=pr_number, sha=head_sha)
    heartbeat.beat(f'gate {gate.name} #{pr_number}')
    group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors='replace', **group)
    try:
        so, se = p.communicate(timeout=gate.timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            so, se = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            so, se = '', ''
        heartbeat.beat(f'gate {gate.name} #{pr_number} timed out')
        return GateResult(gate.name, 'error', f'timed out after {gate.timeout}s (process tree killed)', head_sha,
                          _sha(so or ''))
    heartbeat.beat(f'gate {gate.name} #{pr_number} done')
    return parse(gate, p.returncode, (so or '') + (se or ''), head_sha)


def parse(gate, code, out, head_sha):
    if gate.kind == 'rexcheck-gate':
        return _rexcheck(gate, code, out, head_sha)
    status = 'pass' if code == 0 else 'fail'
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return GateResult(gate.name, status, lines[-1][:200] if lines else f'exit {code}', head_sha, _sha(out),
                      {'tail': out[-TAIL:]})


def from_artifact(cfg, files, head_sha):
    """Gate results from a CI artifact: meta.json + <name>.log + <name>.code per `run = "artifact:<name>"` gate.
    A result for another head (or a missing file) is left out, which the decider treats as pending."""
    import json
    try:
        meta = json.loads(files.get('meta.json', '{}'))
    except ValueError:
        meta = {}
    if meta.get('head_sha') != head_sha:
        return []
    out = []
    for g in cfg.gates:
        if not g.run.startswith('artifact:'):
            continue
        name = g.run.split(':', 1)[1]
        log, code = files.get(f'{name}.log'), files.get(f'{name}.code', '').strip()
        if log is None or not code.lstrip('-').isdigit():
            continue
        out.append(parse(g, int(code), log, head_sha))
    return out


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
