"""Turn (decision, triage, review) into the closed set of things Steward may do.

The actuator (GitHub/Discord) executes only these Action kinds, and only with arguments that
pass validate(). Model output is clamped here:
- labels: only from the allowlist, and never ones that carry authority (none do merges);
- a reviewer/triage escalation can turn merge/wait/request_changes into escalate, never the reverse;
- enqueue_merge exists only when the deterministic decision is merge AND no model flagged anything,
  or (owner-enabled --ai-maintainer) the maintainer approved an escalation that maintainer_merge_ok()
  allows: every gate passed, the only blocking rules are escalations, no hostile content, no hold label.
"""
from dataclasses import dataclass, field

from . import report
from .agents.roles import LABELS

KINDS = ('add_labels', 'upsert_report', 'request_changes', 'ask_owner', 'escalate', 'enqueue_merge')
STATE_LABELS = {'merge': 'steward:queued', 'wait': 'steward:waiting',
                'request_changes': 'steward:changes-needed', 'escalate': 'steward:maintainer'}
HOLD = 'maintainer-hold'   # owner label: this PR is decided by a human only


@dataclass
class Action:
    kind: str
    args: dict = field(default_factory=dict)


def final_action(decision, triage=None, review=None):
    """-> (action, extra_reasons). Models can only add caution."""
    extra = []
    if triage is not None and (triage.hostile_content or triage.needs_human):
        extra.append(('triage', triage.human_reason or ('hostile content' if triage.hostile_content else 'needs a human')))
    if review is not None and review.escalate:
        extra.append(('review', f'{review.escalate_reason}: {review.escalate_detail}'.strip(': ')))
    action = decision.action
    if extra and action != 'escalate':
        action = 'escalate'
    return action, extra


def plan(pr, decision, gates, triage=None, review=None, review_meta=None, progress=None):
    action, extra = final_action(decision, triage, review)
    labels = {STATE_LABELS[action]}
    if triage is not None:
        labels |= {l for l in triage.labels if l in LABELS}
        if triage.hostile_content:
            labels.add('hostile-content')
    out = [Action('add_labels', {'pr': pr.number, 'labels': sorted(labels)})]
    note = review.explanation if review is not None else ''
    if review is not None and review.fix_hints:
        note += '\n\nTry:\n' + '\n'.join(f'- {h}' for h in review.fix_hints)
    body = report.pr_comment(decision, gates, ai_note=note, progress=progress, author=pr.author)
    if extra:
        body += '\n\n**Escalated to a maintainer** (an AI check asked for a human):\n' + '\n'.join(
            f'- {who}: {why}' for who, why in extra)
    out.append(Action('upsert_report', {'pr': pr.number, 'body': body, 'marker': report.MARK}))
    for owner in decision.asks:
        out.append(Action('ask_owner', {'pr': pr.number, 'owner': owner}))
    if action == 'request_changes':
        out.append(Action('request_changes', {'pr': pr.number}))
    elif action == 'escalate':
        out.append(Action('escalate', {'pr': pr.number,
                                       'reasons': [r.message for r in decision.blocking()] + [w for _, w in extra]}))
    elif action == 'merge':
        out.append(Action('enqueue_merge', {'pr': pr.number, 'head_sha': pr.head_sha}))
    return action, out


def maintainer_merge_ok(decision, gates, triage=None, labels=()):
    """-> '' if an AI maintainer approval may merge, else why not. Pure: rules and gate results only."""
    if HOLD in labels:
        return f'label `{HOLD}`: a human decides this one'
    if triage is not None and triage.hostile_content:
        return 'hostile content: a human decides this one'
    if not gates or any(g.status != 'pass' for g in gates):
        return 'not every gate passed'
    if any(r.action not in ('merge', 'escalate') for r in decision.reasons):
        return 'a rule asks for changes or is still waiting'
    return ''


def maintainer_plan(pr, decision, gates, verdict, base_actions, triage=None, labels=(), meta=None):
    """Replace an escalation with the AI maintainer's call. -> (action, actions)."""
    why_not = maintainer_merge_ok(decision, gates, triage, labels) if verdict.decision == 'approve' else ''
    choice = 'defer_to_owner' if why_not else verdict.decision
    model = (meta or {}).get('id') or 'AI'
    note = f'\n\n**Maintainer decision ({model}): {choice.replace("_", " ")}.** {verdict.summary}'
    if why_not:
        note += f'\n\n(It approved, but Steward will not merge on an AI call here: {why_not}.)'
    details = verdict.rationale
    if verdict.concerns:
        details += '\n\nConcerns:\n' + '\n'.join(f'- {c}' for c in verdict.concerns)
    note += f'\n\n<details><summary>Details</summary>\n\n{details}\n\n</details>'
    out = []
    for a in base_actions:
        if a.kind == 'upsert_report':
            a = Action(a.kind, dict(a.args, body=a.args['body'] + note))
        if a.kind == 'add_labels' and choice != 'defer_to_owner':
            state = STATE_LABELS['merge' if choice == 'approve' else 'request_changes']
            a = Action(a.kind, dict(a.args, labels=sorted((set(a.args['labels']) - {STATE_LABELS['escalate']}) | {state})))
        if a.kind == 'escalate' and choice != 'defer_to_owner':
            continue
        out.append(a)
    if choice == 'approve':
        out.append(Action('enqueue_merge', {'pr': pr.number, 'head_sha': pr.head_sha, 'by': 'maintainer'}))
        return 'merge', out
    if choice == 'request_changes':
        out.append(Action('request_changes', {'pr': pr.number}))
        return 'request_changes', out
    return 'escalate', out


def validate(actions, decision, pr, gates=None, triage=None, labels=()):
    """Last line of defence before the actuator; raises on anything outside the contract."""
    for a in actions:
        if a.kind not in KINDS:
            raise ValueError(f'unknown action {a.kind}')
        if a.args.get('pr') != pr.number:
            raise ValueError(f'{a.kind} targets PR {a.args.get("pr")}, expected {pr.number}')
        if a.kind == 'add_labels':
            bad = [l for l in a.args['labels'] if l not in LABELS and l not in STATE_LABELS.values()]
            if bad:
                raise ValueError(f'labels outside the allowlist: {bad}')
        if a.kind == 'enqueue_merge':
            if a.args.get('by') == 'maintainer':
                why_not = maintainer_merge_ok(decision, gates, triage, labels)
                if why_not:
                    raise ValueError(f'maintainer merge not allowed: {why_not}')
            elif decision.action != 'merge':
                raise ValueError('enqueue_merge without a deterministic merge decision')
            if a.args.get('head_sha') != pr.head_sha:
                raise ValueError('enqueue_merge for a different head')
    return actions
