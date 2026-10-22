"""The merge decision. A pure function of facts, gate results, state and policy.

No model output is an input here. A merge happens only when every rule below holds; every
blocking rule is reported, and the strongest one sets the action:
    escalate (a human must look) > request_changes (author must fix) > wait > merge
"""
import re
from dataclasses import dataclass, field

from .config import unit_label
from .state import DAY

ACTIONS = ('merge', 'wait', 'request_changes', 'escalate')
STRENGTH = {'merge': 0, 'wait': 1, 'request_changes': 2, 'escalate': 3}
CHECKED = re.compile(r'^\s*[-*]\s*\[[xX]\]\s*(.+)$', re.M)


@dataclass
class PR:
    number: int
    author: str
    head_sha: str
    files: list                     # changed paths
    body: str = ''
    draft: bool = False
    author_merged_prs: int = 0      # merged PRs by this author in this repo before this one
    is_maintainer: bool = False
    added: list = field(default_factory=list)   # files the PR creates (nobody owns them yet)


@dataclass
class GateResult:
    name: str
    status: str                     # pass | fail | error | pending
    summary: str = ''
    head_sha: str = ''              # commit the gate ran on; a stale result counts as pending
    output_sha256: str = ''
    details: dict = field(default_factory=dict)


@dataclass
class Reason:
    action: str
    rule: str
    message: str


@dataclass
class Decision:
    action: str
    reasons: list
    units: list                     # retail addresses the PR touches
    asks: list = field(default_factory=list)   # owners Steward must ask for consent (owner rule)

    def blocking(self):
        return [r for r in self.reasons if r.action != 'merge']


def decide(pr, gates, state, cfg, sizes, now):
    pol = cfg.policy
    reasons = []
    asks = []

    def block(action, rule, msg):
        reasons.append(Reason(action, rule, msg))

    units = sorted({u for u in (cfg.unit_of(f) for f in pr.files) if u is not None}, key=str)

    if pr.draft:
        block('wait', 'draft', 'PR is a draft')

    protected = [f for f in pr.files if cfg.is_protected(f)]
    if protected:
        block('escalate', 'protected-path',
              'touches rules/CI/checker files, a maintainer decides: ' + ', '.join(protected[:5]))

    if len(units) > pol.max_units_per_pr:
        block('request_changes', 'too-big', f'{len(units)} functions, the limit is {pol.max_units_per_pr} per PR')

    unknown = [u for u in units if sizes and isinstance(u, int) and u not in sizes]   # new files are normal
    if unknown:
        block('request_changes', 'unknown-unit',
              'not in the function table: ' + ', '.join(unit_label(u) for u in unknown[:5]))

    ticked = [m.strip().lower() for m in CHECKED.findall(pr.body or '')]
    missing = [box for box in pol.required_checkboxes if not any(box.lower() in t for t in ticked)]
    if missing:
        block('request_changes', 'checklist', 'unticked: ' + '; '.join(missing))
    if pol.made_by_checkboxes:
        made = [box for box in pol.made_by_checkboxes if any(box.lower() in t for t in ticked)]
        if len(made) != 1:
            block('request_changes', 'made-by', 'tick exactly one of: ' + '; '.join(pol.made_by_checkboxes))
        elif pol.agent_marker and made[0].lower() == pol.agent_marker.lower():
            unacked = [box for box in pol.agent_checkboxes if not any(box.lower() in t for t in ticked)]
            if unacked:
                block('request_changes', 'agent-ack', 'agent boxes unticked: ' + '; '.join(unacked))

    # claims: someone else's claim always wins (halo2: lanes make way for contributors)
    for u in units:
        c = state.holder(u, sizes.get(u, 1))
        if c and c.user != pr.author:
            block('request_changes', 'claimed', f'{unit_label(u)} is claimed by {c.user} (claim #{c.id})')
        elif not c and pol.require_claim and not pr.is_maintainer:
            block('request_changes', 'unclaimed', f'claim {unit_label(u)} first (/claim)')

    # owner rule: changing someone else's merged function
    owned = [u for u in units if not (isinstance(u, str) and u in pr.added)]
    exempt = pol.owner_rule_exempt_maintainers and pr.is_maintainer
    for owner in sorted({state.owner(u) for u in owned} - {None, pr.author}) if not exempt else []:
        key = f'{pr.number}:{owner}'
        if key in state.consents:
            continue
        asked = state.asks.get(key)
        if asked is None:
            asks.append(owner)
            block('wait', 'owner-rule', f'changes merged work by {owner}; asking them first')
        elif now - asked < pol.owner_rule_days * DAY:
            left = pol.owner_rule_days - int((now - asked) // DAY)
            block('wait', 'owner-rule', f'waiting for {owner} (asked; {left} day(s) left)')

    # gates, only results for the current head count
    by_name = {g.name: g for g in gates}
    for g in cfg.gates:
        if not g.required:
            continue
        r = by_name.get(g.name)
        if r is None or r.status == 'pending' or (r.head_sha and r.head_sha != pr.head_sha):
            block('wait', 'gate-pending', f'{g.name} has not run on {pr.head_sha[:8]}')
        elif r.status == 'fail':
            block('request_changes', 'gate-fail', f'{g.name}: {r.summary or "failed"}')
        elif r.status == 'error':
            block('escalate', 'gate-error', f'{g.name} broke ({r.summary or "no summary"}), not the PR\'s fault?')
        elif r.status != 'pass':
            block('escalate', 'gate-status', f'{g.name}: unknown status {r.status!r}')

    # progress: gates that report per-unit gains (details['better']) must show at least one
    reporting = [by_name[g.name] for g in cfg.gates if g.name in by_name and by_name[g.name].status == 'pass'
                 and 'better' in by_name[g.name].details]
    if pol.require_progress and units and reporting and not any(r.details['better'] for r in reporting):
        block('request_changes', 'no-progress', 'no function got closer to retail (no new or better verdicts)')

    if not units and not protected:
        block('escalate', 'no-units', 'changes no decomp unit; a maintainer decides')

    if pol.first_timer_needs_human and pr.author_merged_prs == 0 and not pr.is_maintainer:
        block('escalate', 'first-timer', f'first PR by {pr.author}: a human does the first merge')

    if not pol.auto_merge:
        block('escalate', 'auto-merge-off', 'auto-merge is off for this project')

    action = max((r.action for r in reasons), key=STRENGTH.get, default='merge')
    if action == 'merge':
        reasons.append(Reason('merge', 'all-green', 'every required gate passed and no rule blocks'))
    return Decision(action, reasons, units, asks)
