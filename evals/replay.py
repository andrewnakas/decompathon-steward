#!/usr/bin/env python3
"""Replay Steward's rules over a repo's real PR history (read-only: gh pr list).

Gates are assumed to have passed (history does not say), so this measures the *rule* layer:
claims are unknown, owners are rebuilt from earlier merges (by unit), consent asks are assumed
posted when the PR opened. Compares Steward's action with what the maintainer did.

  python evals/replay.py --config examples/halo2/steward.toml [--limit 1000] [--out work/replay.md]
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steward import config  # noqa: E402
from steward.decider import PR, GateResult, decide  # noqa: E402
from steward.state import State  # noqa: E402


def ts(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() if s else None


QUERY = """query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
pullRequests(first:25,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor}
nodes{number title body state isDraft createdAt mergedAt closedAt author{login} files(first:100){nodes{path changeType}}}}}}"""


def fetch(repo, limit):
    """Small GraphQL pages (a single big gh pr list --json files call times out with 504)."""
    owner, name = repo.split('/')
    prs, after = [], None
    while len(prs) < limit:
        args = ['gh', 'api', 'graphql', '-f', f'query={QUERY}', '-F', f'owner={owner}', '-F', f'name={name}']
        if after:
            args += ['-F', f'after={after}']
        for attempt in range(3):
            r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
            if r.returncode == 0:
                break
        r.check_returncode()
        page = json.loads(r.stdout)['data']['repository']['pullRequests']
        for n in page['nodes']:
            n['files'] = n['files']['nodes'] if n['files'] else []
            prs.append(n)
        if not page['pageInfo']['hasNextPage']:
            break
        after = page['pageInfo']['endCursor']
    return prs[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--limit', type=int, default=1000)
    ap.add_argument('--cache', help='read/write the gh JSON here')
    ap.add_argument('--out')
    a = ap.parse_args()
    cfg = config.load(a.config)
    sizes = cfg.load_sizes()
    if a.cache and os.path.exists(a.cache):
        prs = json.load(open(a.cache, encoding='utf-8'))
    else:
        prs = fetch(cfg.repo, a.limit)
        if a.cache:
            json.dump(prs, open(a.cache, 'w', encoding='utf-8'))
    prs.sort(key=lambda p: p['createdAt'])
    merges = sorted(((ts(p['mergedAt']), p) for p in prs if p['mergedAt']), key=lambda m: m[0])

    table = collections.Counter()
    rules = collections.Counter()
    rows = []
    for p in prs:
        actual = 'merged' if p['mergedAt'] else ('closed' if p['state'] == 'CLOSED' else 'open')
        when = ts(p['mergedAt'] or p['closedAt']) or datetime.datetime.now().timestamp()
        author = (p.get('author') or {}).get('login', 'ghost')
        st = State()
        prior = 0
        for t, m in merges:
            if t >= when or m['number'] == p['number']:
                continue
            mauthor = (m.get('author') or {}).get('login', 'ghost')
            prior += mauthor == author
            st.record_merge(mauthor, [u for u in (cfg.unit_of(f['path']) for f in m['files']) if u is not None])
        for owner in set(st.owners.values()):
            st.asks[f'{p["number"]}:{owner}'] = ts(p['createdAt'])
        head = 'r' * 40
        pr = PR(p['number'], author, head, [f['path'] for f in p['files']], p.get('body') or '',
                False, prior, author in cfg.policy.maintainers,
                [f['path'] for f in p['files'] if f.get('changeType') == 'ADDED'])   # drafts are judged on content; the history only shows the final state
        gates = [GateResult(g.name, 'pass', 'assumed', head) for g in cfg.gates]
        d = decide(pr, gates, st, cfg, sizes, when)
        table[(actual, d.action)] += 1
        for r in d.blocking():
            rules[(actual, r.rule)] += 1
        rows.append((p['number'], actual, d.action, author, p['title'][:60],
                     '; '.join(f'{r.rule}: {r.message}' for r in d.blocking())[:240]))

    acts = ('merge', 'wait', 'request_changes', 'escalate')
    lines = [f'# Replay: {cfg.name} ({len(prs)} PRs, gates assumed pass)', '',
             '| maintainer vs steward | ' + ' | '.join(acts) + ' |', '|---|' + '---|' * len(acts)]
    for actual in ('merged', 'closed', 'open'):
        lines.append(f'| {actual} | ' + ' | '.join(str(table[(actual, x)]) for x in acts) + ' |')
    lines += ['', '## Blocking rules by maintainer outcome', '']
    lines += [f'- {actual} / {rule}: {n}' for (actual, rule), n in sorted(rules.items())]
    lines += ['', '## Every PR', '', '| # | maintainer | steward | author | title | why |', '|---|---|---|---|---|---|']
    lines += [f'| {n} | {act} | {s} | {au} | {t} | {why} |' for n, act, s, au, t, why in rows]
    text = '\n'.join(lines)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
        open(a.out, 'w', encoding='utf-8').write(text)
    print('\n'.join(lines[:12 + len(rules)]))


if __name__ == '__main__':
    main()
