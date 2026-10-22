"""The one PR comment Steward keeps edited, and the pinned claims issue.

The comment is written for people, not for the rules engine: what happened, why, what (if anything) to do next,
and what the PR does for the project's progress. Everything in it comes from the deterministic decision and the gate
results; model text only ever appears inside the clearly labelled AI section.
"""
import re
import time

from .config import unit_label

MARK = '<!-- steward:report -->'

HEADLINE = {
    'merge': "Nice work! Every check passed, so I'm merging this now.",
    'wait': "Thanks! This one has to wait a little. Here's why:",
    'request_changes': "Thanks for this! It needs a change or two before it can go in:",
    'escalate': "Thanks! This one needs a human maintainer, so I've asked one to take a look:",
}

# plain-language versions of the decider's rules (its own message follows as the detail)
EXPLAIN = {
    'draft': 'This PR is still a draft. Mark it "Ready for review" when you want me to check it.',
    'gate-pending': "The checks are still running. I'll update this comment when they finish.",
    'owner-rule': ("This changes a function someone else already matched. I've asked them about it; if they don't "
                   "object within the waiting period I'll go ahead, and they keep their credit."),
    'auto-merge-off': 'Auto-merge is switched off for this project, so a maintainer will do the merge.',
    'first-timer': ("This is your first PR here, so a maintainer gives it a quick look before it goes in. "
                    "After your first merge I can merge your PRs on my own."),
    'gate-error': "One of the checks broke on my side. That's not your fault; a maintainer will look into it.",
    'gate-status': "A check came back in a state I don't understand, so I've asked a maintainer.",
    'no-units': "I couldn't find any function files in this PR, so a maintainer will review it by hand.",
    'protected-path': ('This touches project tooling, config or rules. Only maintainers change those, '
                       'so one will review it.'),
    'checklist': ('Please tick every provenance box in the PR description. They confirm the work is yours, '
                  'that no leaked material or game data is in it, and that you decompiled lawfully from a copy of '
                  'the game you own, using tools you may legally use.'),
    'made-by': 'Please tick exactly one box saying who made this PR: you, or an AI agent working for you.',
    'agent-ack': ("This PR says an AI agent prepared it, so the agent has to tick the agent acknowledgement too, "
                  "on every PR: it checked each statement against this PR's actual changes, and its human is "
                  "responsible for the contribution."),
    'claimed': 'Someone else has claimed one of these functions. Please check with them first (`/status` shows claims).',
    'unclaimed': 'Please claim these functions first (`/claim 0xADDR`) so nobody else works on them at the same time.',
    'gate-fail': "A check failed. The details above show which function and where its bytes differ from the game.",
    'no-progress': "This PR doesn't add or improve any matched function, so there's nothing for me to merge.",
    'too-big': 'This PR has more functions than one PR may carry. Please split it into smaller PRs.',
    'unknown-unit': ("One of the files doesn't name a function from the project's function table. Check the address "
                     "in the file name."),
}

GATE_TEXT = {
    'match': ('every function file compiles to exactly the same bytes as the original game',
              "at least one function doesn't compile to the original bytes yet"),
    'build': ('the whole game code still rebuilds byte-for-byte identical to the original',
              'the full rebuild no longer comes out identical to the original'),
    'oracle': ('every function reproduces the original behaviour', "a function doesn't reproduce the original behaviour"),
    'scan': ('no game files or forbidden code found', 'the safety scan found something that is not allowed'),
}


def _pct(a, b):
    return f'{100.0 * a / b:.2f}%' if b else '0%'


def progress_line(p):
    if not p or not p.get('total'):
        return ''
    new, before, total = p['new'], p['before'], p['total']
    after = before + new
    if new <= 0:
        return f'The project stands at **{before:,} / {total:,}** functions matched ({_pct(before, total)}).'
    s = 's' if new != 1 else ''
    line = (f'This PR matches **{new}** new function{s}, taking the project from **{before:,} / {total:,}** '
            f'to **{after:,} / {total:,}** functions matched ({_pct(before, total)} -> {_pct(after, total)})')
    if p.get('bytes_total'):
        line += f'; by code size the project is {_pct(p["bytes_before"] + p["bytes_new"], p["bytes_total"])} done'
    return line + '.'


def _gate_line(g):
    good, bad = GATE_TEXT.get(g.name, (f'the {g.name} check passed', f'the {g.name} check failed'))
    if g.status == 'pass':
        return f'- **{g.name}** passed: {good}.'
    if g.status == 'fail':
        lines = [f'- **{g.name}** failed: {bad}.']
        tail = g.details.get('tail', '')
        for p in re.findall(r'^PROBLEM (.+)$', tail, re.M)[:5]:      # match gate: which file and why
            lines.append(f'  - {p.strip()}')
        diffs = re.findall(r'(0x[0-9A-Fa-f]{8}): built ([0-9A-Fa-f]{8}) game ([0-9A-Fa-f]{8})', tail + g.summary)
        for a, built, game in list(dict.fromkeys(diffs))[:3]:
            lines.append(f'  - at `{a}` your code produced the instruction word `{built}` where the game has `{game}`')
        if len(lines) == 1:
            lines[0] += f' Detail: `{g.summary.strip()}`'
        return '\n'.join(lines)
    if g.status == 'pending':
        return f'- **{g.name}** is still running.'
    return f"- **{g.name}** hit a problem on my side (not your fault): `{g.summary.strip()}`"


def pr_comment(decision, gates, ai_note='', progress=None, author=None):
    out = [MARK, f'Hi @{author}!' if author else 'Hi!', '', HEADLINE[decision.action], '']
    line = progress_line(progress) if decision.action == 'merge' else ''
    if line:
        out += [line, '']
    if decision.units:
        out.append('Functions in this PR: ' + ', '.join(f'`{unit_label(u)}`' for u in decision.units))
        out.append('')
    if gates:
        out.append('**What I checked**')
        out += [_gate_line(g) for g in gates]
        out.append('')
    for g in gates:
        worse = g.details.get('worse') or []
        if worse:
            out.append(f'**{len(worse)} function(s) that used to match stopped matching** (nothing that already '
                       f'works may break):')
            out += [f'- `0x{w["addr"]}` {w["was"]} -> {w["now"]}' for w in worse[:20]]
            out.append('')
    blocking = decision.blocking()
    if blocking:
        out.append('**What happens next**')
        for r in blocking:
            text = EXPLAIN.get(r.rule, r.message)
            out.append(f'- {text}' + (f' _({r.message})_' if r.rule in EXPLAIN and r.message else ''))
        out.append('')
    if ai_note:
        # model text is quoted and labelled, never mixed with the deterministic part
        out.append('<details><summary>A longer explanation (written by AI)</summary>\n')
        out += ['> ' + l for l in ai_note.strip().splitlines()]
        out.append('\n</details>\n')
    out.append("<sub>I'm Steward, this project's automated maintainer. Merges follow fixed rules, never AI "
               'judgement. Think I got it wrong? Open an issue with the `appeal` label and a human will look.</sub>')
    return '\n'.join(out)


def claims_issue(state, now=None, lapse_days=7, warn_days=5):
    now = now or time.time()
    out = ['<!-- steward:claims -->', '## Active claims', '',
           'Claim with `/claim 0xADDR SIZE` or `/claim path/to/file` (here or on Discord). '
           f'Claims lapse after {lapse_days} days without activity (I post a reminder after {warn_days}).', '',
           '| # | Who | What | Last activity |', '|---|---|---|---|']
    for c in sorted(state.claims, key=lambda c: (c.start, c.path)):
        days = int((now - c.last_activity) // 86400)
        out.append(f'| {c.id} | @{c.user} | {c.label()} | {days} day(s) ago |')
    if not state.claims:
        out.append('| | | nothing claimed | |')
    return '\n'.join(out)
