"""steward CLI. Offline: it decides and prints, it never talks to GitHub (that is the actuator, M4).

  python -m steward check-config steward.toml
  python -m steward decide  --config steward.toml --pr pr.json --gates gates.json --state state.json [--audit audit.jsonl]
  python -m steward claim   --config steward.toml --state state.json USER (0xADDR SIZE | --path P)
  python -m steward release --state state.json USER ID
  python -m steward sweep   --config steward.toml --state state.json
  python -m steward claims-issue --state state.json
  python -m steward audit-verify audit.jsonl
  python -m steward next    --config steward.toml [--state state.json] [-n 20]
"""
import argparse
import json
import sys
import time

from . import audit, config, report, roadmap
from .decider import PR, GateResult, decide
from .state import ClaimError, State


def _cfg(a):
    return config.load(a.config)


def cmd_check_config(a):
    c = config.load(a.file)
    sizes = c.load_sizes()
    print(f'{c.name} ({c.repo})  root={c.root}  config sha256 {c.digest[:12]}')
    print(f'function table: {len(sizes)} units, {sum(sizes.values())} bytes' if sizes else 'function table: none')
    for g in c.gates:
        print(f'  gate {g.name:10s} {g.where:6s} {g.kind:14s} {"required" if g.required else "optional"}  {g.run}')
    p = c.policy
    print(f'policy: auto_merge={p.auto_merge} first_timer_needs_human={p.first_timer_needs_human} '
          f'owner_rule={p.owner_rule_days}d claims<=0x{p.claim_max_bytes:X}B x{p.claim_max_open} '
          f'lapse={p.claim_lapse_days}d max_units={p.max_units_per_pr}')
    print(f'protected: {", ".join(p.protected_paths)}')


def cmd_decide(a):
    c = _cfg(a)
    pr = PR(**json.load(open(a.pr, encoding='utf-8')))
    gates = [GateResult(**g) for g in json.load(open(a.gates, encoding='utf-8'))] if a.gates else []
    st = State.load(a.state) if a.state else State()
    now = a.now or time.time()
    d = decide(pr, gates, st, c, c.load_sizes(), now)
    print(f'PR #{pr.number} by {pr.author}: {d.action.upper()}')
    for r in d.reasons:
        print(f'  [{r.action}] {r.rule}: {r.message}')
    if d.asks:
        print('  ask consent from: ' + ', '.join(d.asks))
    if a.comment:
        print('\n' + report.pr_comment(d, gates))
    if a.audit:
        e = audit.append(a.audit, pr, d, gates, c, now=now)
        print(f'audit: {e["hash"][:12]}')


def cmd_claim(a):
    c = _cfg(a)
    st = State.load(a.state)
    try:
        if a.path:
            cl = st.claim(a.user, c.policy, time.time(), path=a.path)
        else:
            cl = st.claim(a.user, c.policy, time.time(), start=int(a.addr, 16), size=int(a.size, 0))
    except ClaimError as e:
        print(f'refused: {e}')
        sys.exit(1)
    st.save(a.state)
    print(f'claim #{cl.id}: {cl.user} {cl.label()}')


def cmd_release(a):
    st = State.load(a.state)
    try:
        cl = st.release(a.id, a.user)
    except ClaimError as e:
        print(f'refused: {e}')
        sys.exit(1)
    st.save(a.state)
    print(f'released #{cl.id} ({cl.label()})')


def cmd_sweep(a):
    c = _cfg(a)
    st = State.load(a.state)
    expired, warn = st.sweep(c.policy, a.now or time.time())
    st.save(a.state)
    print(f'{len(expired)} expired, {len(warn)} warned')
    for cl in expired:
        print(f'  expired #{cl.id} {cl.user} {cl.label()}')
    for cl in warn:
        print(f'  warn    #{cl.id} {cl.user} {cl.label()}')


def cmd_claims_issue(a):
    print(report.claims_issue(State.load(a.state)))


def cmd_audit_verify(a):
    ok, n, bad = audit.verify(a.file)
    print(f'{n} entries, chain {"OK" if ok else f"BROKEN at line {bad}"}')
    sys.exit(0 if ok else 1)


def cmd_next(a):
    c = _cfg(a)
    st = State.load(a.state) if a.state else State()
    rows = roadmap.load_table(c)
    done = roadmap.done_from_verdicts(c)
    s = roadmap.stats(rows, done)
    print(f"{c.name}: {s['done_functions']}/{s['functions']} functions, {s['done_bytes']}/{s['bytes']} bytes ({s['percent']:.3f}%)")
    for score, addr, size, name, near in roadmap.suggest(rows, done, lambda x, n: st.holder(x, n) is not None, a.n):
        print(f'  0x{addr:08X} {size:6d} B  score {score:5.2f}  {name}{"  (next to done work)" if near else ""}')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='steward')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('check-config'); s.add_argument('file'); s.set_defaults(f=cmd_check_config)
    s = sub.add_parser('decide')
    s.add_argument('--config', required=True); s.add_argument('--pr', required=True)
    s.add_argument('--gates'); s.add_argument('--state'); s.add_argument('--audit')
    s.add_argument('--now', type=float); s.add_argument('--comment', action='store_true')
    s.set_defaults(f=cmd_decide)
    s = sub.add_parser('claim')
    s.add_argument('--config', required=True); s.add_argument('--state', required=True)
    s.add_argument('user'); s.add_argument('addr', nargs='?'); s.add_argument('size', nargs='?')
    s.add_argument('--path'); s.set_defaults(f=cmd_claim)
    s = sub.add_parser('release')
    s.add_argument('--state', required=True); s.add_argument('user'); s.add_argument('id', type=int)
    s.set_defaults(f=cmd_release)
    s = sub.add_parser('sweep')
    s.add_argument('--config', required=True); s.add_argument('--state', required=True)
    s.add_argument('--now', type=float); s.set_defaults(f=cmd_sweep)
    s = sub.add_parser('claims-issue'); s.add_argument('--state', required=True); s.set_defaults(f=cmd_claims_issue)
    s = sub.add_parser('audit-verify'); s.add_argument('file'); s.set_defaults(f=cmd_audit_verify)
    s = sub.add_parser('next')
    s.add_argument('--config', required=True); s.add_argument('--state'); s.add_argument('-n', type=int, default=20)
    s.set_defaults(f=cmd_next)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == '__main__':
    main()
