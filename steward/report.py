"""The one PR comment Steward keeps edited (decomp-dev bot style), and the pinned claims issue."""
import time

MARK = '<!-- steward:report -->'
ICON = {'merge': 'queued for merge', 'wait': 'waiting', 'request_changes': 'changes needed',
        'escalate': 'a maintainer will look'}
GICON = {'pass': 'pass', 'fail': 'FAIL', 'error': 'harness error', 'pending': 'pending'}


def pr_comment(decision, gates, ai_note=''):
    out = [MARK, f'### Steward (automated) - {ICON[decision.action]}', '']
    if decision.units:
        out.append('Functions: ' + ', '.join(f'`0x{u:08X}`' for u in decision.units))
        out.append('')
    if gates:
        out += ['| Gate | Result | Summary |', '|---|---|---|']
        out += [f'| {g.name} | {GICON.get(g.status, g.status)} | {g.summary} |' for g in gates]
        out.append('')
    for g in gates:
        worse = g.details.get('worse') or []
        if worse:
            out.append(f'**{g.name}: {len(worse)} function(s) stopped matching** (zero-loss rule):')
            out += [f'- `0x{w["addr"]}` {w["was"]} -> {w["now"]}' for w in worse[:20]]
            out.append('')
    blocking = decision.blocking()
    if blocking:
        out.append('What blocks the merge:')
        out += [f'- **{r.rule}**: {r.message}' for r in blocking]
        out.append('')
    if ai_note:
        # model text is quoted and labelled, never mixed with the deterministic part
        out.append('<details><summary>Steward (AI) explains</summary>\n')
        out += ['> ' + l for l in ai_note.strip().splitlines()]
        out.append('\n</details>\n')
    out.append('_Decided by a deterministic rule check; the AI note only explains. '
               'Disagree? Open an issue with the `appeal` label._')
    return '\n'.join(out)


def claims_issue(state, now=None):
    now = now or time.time()
    out = ['<!-- steward:claims -->', '## Active claims', '',
           'Claim with `/claim 0xADDR SIZE` or `/claim path/to/file` (here or on Discord). '
           'Claims lapse after 14 days without activity.', '',
           '| # | Who | What | Last activity |', '|---|---|---|---|']
    for c in sorted(state.claims, key=lambda c: (c.start, c.path)):
        days = int((now - c.last_activity) // 86400)
        out.append(f'| {c.id} | @{c.user} | {c.label()} | {days} day(s) ago |')
    if not state.claims:
        out.append('| | | nothing claimed | |')
    return '\n'.join(out)
