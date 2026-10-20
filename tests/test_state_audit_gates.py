import json

import pytest

from steward import audit, report
from steward.decider import PR, GateResult, decide
from steward.gates import parse
from steward.state import DAY, ClaimError, State

NOW = 1_800_000_000.0


# -- claims ------------------------------------------------------------------
def test_claim_limits(cfg):
    st = State()
    with pytest.raises(ClaimError):
        st.claim('a', cfg.policy, NOW, start=0x82000000, size=0x2001)
    st.claim('a', cfg.policy, NOW, start=0x82000000, size=0x100)
    with pytest.raises(ClaimError, match='overlaps'):
        st.claim('b', cfg.policy, NOW, start=0x820000F0, size=0x20)
    st.claim('b', cfg.policy, NOW, start=0x82000100, size=0x20)     # adjacent is fine
    st.claim('a', cfg.policy, NOW, path='decomp/x.cpp')
    st.claim('a', cfg.policy, NOW, start=0x82010000, size=4)
    with pytest.raises(ClaimError, match='already holds'):
        st.claim('a', cfg.policy, NOW, start=0x82020000, size=4)


def test_release_only_by_holder(cfg):
    st = State()
    c = st.claim('a', cfg.policy, NOW, start=0x82000000, size=4)
    with pytest.raises(ClaimError):
        st.release(c.id, 'b')
    st.release(c.id, 'a')
    assert not st.claims


def test_sweep_warns_then_expires(cfg):
    st = State()
    st.claim('a', cfg.policy, NOW, start=0x82000000, size=4)
    assert st.sweep(cfg.policy, NOW + 9 * DAY) == ([], [])
    exp, warn = st.sweep(cfg.policy, NOW + 10 * DAY)
    assert not exp and len(warn) == 1
    st.touch('a', NOW + 10 * DAY)
    assert st.sweep(cfg.policy, NOW + 19 * DAY) == ([], [])
    exp, _ = st.sweep(cfg.policy, NOW + 24 * DAY)
    assert len(exp) == 1 and not st.claims


def test_state_roundtrip(cfg, tmp_path):
    st = State()
    st.claim('a', cfg.policy, NOW, start=0x82000000, size=4)
    st.record_merge('a', [0x82000040])
    p = str(tmp_path / 's.json')
    st.save(p)
    st2 = State.load(p)
    assert st2.holder(0x82000000).user == 'a' and st2.owner(0x82000040) == 'a' and st2.next_id == 2


def test_claims_issue_render(cfg):
    st = State()
    st.claim('a', cfg.policy, NOW, start=0x82000000, size=0x40)
    assert '@a' in report.claims_issue(st, NOW) and '0x82000000-0x82000040' in report.claims_issue(st, NOW)


# -- audit -------------------------------------------------------------------
def test_audit_chain(cfg, tmp_path):
    p = str(tmp_path / 'audit.jsonl')
    pr = PR(1, 'a', 'f' * 40, ['decomp/sub_82000000.cpp'], author_merged_prs=1)
    gates = [GateResult('scan', 'pass', '', 'f' * 40), GateResult('oracle', 'pass', '', 'f' * 40)]
    for i in range(3):
        d = decide(pr, gates, State(), cfg, cfg.load_sizes(), NOW)
        audit.append(p, pr, d, gates, cfg, now=NOW + i)
    assert audit.verify(p) == (True, 3, None)
    lines = open(p).read().splitlines()
    e = json.loads(lines[1])
    e['action'] = 'escalate'
    lines[1] = json.dumps(e, sort_keys=True)
    open(p, 'w').write('\n'.join(lines) + '\n')
    assert audit.verify(p)[0] is False
    del lines[1]
    open(p, 'w').write('\n'.join(lines) + '\n')
    assert audit.verify(p)[0] is False


# -- gates -------------------------------------------------------------------
REX_PASS = '''mutants: 4/4 caught
  ok   0x8296EA10  differs=8 equal=0 untestable=0
zero-loss vs games/skate3/verdicts.csv: 0 WORSE, 1 better/new
  +     0x82B92E50: new -> behaviour
GATE PASS
'''
REX_WORSE = '''mutants: 4/4 caught
zero-loss vs games/skate3/verdicts.csv: 1 WORSE, 0 better/new
  WORSE 0x826C20B0: behaviour -> differs
GATE FAIL
'''
REX_BLIND = '''mutants: 3/4 caught
  MISS 0x826C20B0  differs=0 equal=5 untestable=0
zero-loss vs games/skate3/verdicts.csv: 0 WORSE, 0 better/new
GATE FAIL
'''


def test_rexcheck_parse(cfg):
    g = cfg.gates[1]
    r = parse(g, 0, REX_PASS, 'x')
    assert r.status == 'pass' and r.details['better'][0]['addr'] == '82B92E50'
    r = parse(g, 1, REX_WORSE, 'x')
    assert r.status == 'fail' and r.details['worse'] == [{'addr': '826C20B0', 'was': 'behaviour', 'now': 'differs'}]
    assert '826C20B0' in report.pr_comment(decide(
        PR(1, 'a', 'x', ['decomp/sub_82000000.cpp'], author_merged_prs=1), [r], State(), cfg, {}, NOW), [r])
    assert parse(g, 1, REX_BLIND, 'x').status == 'error'
    assert parse(g, 0, 'Traceback ...', 'x').status == 'error'
    assert parse(g, 1, REX_PASS, 'x').status == 'fail'     # PASS text but nonzero exit is not a pass


def test_command_gate_runs(cfg, tmp_path):
    from steward.gates import run
    r = run(cfg.gates[0], str(tmp_path), 'x')
    assert r.status == 'pass' and r.summary == 'ok' and len(r.output_sha256) == 64


def test_comment_quotes_ai_text(cfg):
    pr = PR(1, 'a', 'x', ['decomp/sub_82000000.cpp'], author_merged_prs=1)
    d = decide(pr, [], State(), cfg, {}, NOW)
    c = report.pr_comment(d, [], ai_note='line one\n## fake heading')
    assert '> ## fake heading' in c and c.startswith(report.MARK)
