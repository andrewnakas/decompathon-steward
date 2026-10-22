"""The Steward service for one repo: poll GitHub, decide, act. Runs on the judge box / owner's PC.

  python -m steward.daemon --repo owner/name --app <slug> [--data DIR] [--once] [--no-ai] [--interval 60]

Each tick:
 1. refresh config + function table from the repo's default branch (policy = what is merged);
 2. slash commands in new comments (/claim /unclaim /consent /next /status /link);
 3. claims sweep (warn at day 10, expire at 14) + pinned claims issue;
 4. every open PR: facts -> gates (CI artifact for the current head) -> decide -> Claude triage/review
    (once per head) -> clamp -> act (labels, one edited report comment, owner asks, merge);
 5. return events (merged, escalated, claims, ...) for Discord.
Nothing is acted on twice: a PR is re-acted on only when its plan changes.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import time
import traceback

from . import actions, audit, commands, config, report, roadmap
from .agents import roles
from .decider import PR, decide
from . import judge_runner
from .gates import from_artifact
from .github import App, GitHubError, Repo
from .state import DAY, State

LABEL_COLORS = {'steward:queued': '0e8a16', 'steward:waiting': 'fbca04', 'steward:changes-needed': 'd93f0b',
                'steward:maintainer': '5319e7', 'hostile-content': 'b60205', 'needs-maintainer': '5319e7',
                'needs-provenance': 'c5def5', 'good-first-function': '7057ff', 'decomp': '1d76db',
                'tooling': 'bfdadc', 'docs': '0075ca', 'question': 'd876e3', 'bug': 'd73a4a', 'claim': 'c2e0c6',
                'duplicate': 'cfd3d7', 'spam': '000000'}
LINK = re.compile(r'^\s*/link\s+([A-Za-z0-9]{6,32})\s*$', re.M)
CLAIMS_MARK = '<!-- steward:claims -->'


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Steward:
    def __init__(self, repo, data_dir, use_ai=True, log=print, judge=None):
        self.repo = repo
        self.judge = judge          # judge_runner.JudgeRunner: runs where=judge gates on this machine
        self.data = data_dir
        os.makedirs(os.path.join(data_dir, 'repo'), exist_ok=True)
        self.state_path = os.path.join(data_dir, 'state.json')
        self.memo_path = os.path.join(data_dir, 'daemon.json')
        self.audit_path = os.path.join(data_dir, 'audit.jsonl')
        self.state = State.load(self.state_path)
        self.memo = json.load(open(self.memo_path)) if os.path.exists(self.memo_path) else {
            'cursor': iso(time.time() - 3600), 'prs': {}, 'links': {}, 'link_codes': {}, 'claims_issue': None,
            'merged_counts': {}, 'owners_bootstrapped': False}
        self.use_ai = use_ai
        self.log = log
        self.cfg = None
        self.sizes = {}

    def save(self):
        self.state.save(self.state_path)
        tmp = self.memo_path + '.tmp'
        json.dump(self.memo, open(tmp, 'w'), indent=1, sort_keys=True)
        os.replace(tmp, self.memo_path)

    # -- config ----------------------------------------------------------
    def refresh_config(self):
        info = self.repo.get('')
        branch = info['default_branch']
        d = os.path.join(self.data, 'repo')
        text = self.repo.file_text('steward.toml', branch)
        open(os.path.join(d, 'steward.toml'), 'w', encoding='utf-8', newline='').write(text)
        cfg = config.load(os.path.join(d, 'steward.toml'))
        if cfg.function_table:
            table = self.repo.file_text(cfg.function_table, branch)
            path = os.path.join(d, *cfg.function_table.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'w', encoding='utf-8').write(table)
        self.cfg, self.branch = cfg, branch
        self._base_units = None
        self.sizes = cfg.load_sizes()

    def bootstrap_owners(self):
        """Owners of functions merged before Steward existed: the @author header on the default branch."""
        if self.memo.get('owners_bootstrapped'):
            return
        for t in self.repo.tree(self.branch):
            u = self.cfg.unit_of(t['path']) if t['type'] == 'blob' else None
            if u is None:
                continue
            m = re.search(r'//\s*@author\s+(\S+)', self.repo.file_text(t['path'], self.branch))
            if m:
                self.state.record_merge(m.group(1), [u])
        self.memo['owners_bootstrapped'] = True

    def is_maintainer(self, login):
        return login in self.cfg.policy.maintainers

    # -- comments --------------------------------------------------------
    def poll_commands(self, events):
        now = time.time()
        newest = self.memo['cursor']
        for c in self.repo.comments_since(self.memo['cursor']):
            newest = max(newest, c['created_at'])
            actor = c['user']['login']
            if actor == self.repo.app.login or c['user'].get('type') == 'Bot':
                continue
            if str(c['id']) in self.memo.get('seen', []):    # `since` is inclusive; never answer twice
                continue
            self.memo.setdefault('seen', []).append(str(c['id']))
            self.memo['seen'] = self.memo['seen'][-500:]
            n = int(c['issue_url'].rsplit('/', 1)[1])
            replies = []
            m = LINK.search(c['body'])
            if m:
                did = self.memo['link_codes'].pop(m.group(1), None)
                if did:
                    self.memo['links'][str(did)] = actor
                    replies.append(f'linked your Discord account to @{actor}')
                    events.append({'kind': 'linked', 'discord_id': did, 'login': actor})
            for cmd, args in commands.parse(c['body']):
                if cmd == 'next':
                    replies.append(self.next_text())
                    continue
                if cmd == 'status':
                    replies.append(self.status_text())
                    continue
                r = commands.run(cmd, args, actor, self.state, self.cfg, self.sizes, now, self.is_maintainer(actor))
                if r:
                    replies.append(r)
                    if cmd in ('claim', 'unclaim') and not r.startswith('refused'):
                        events.append({'kind': cmd, 'text': f'@{actor}: {r}'})
            if replies:
                self.state.touch(actor, now)
                self.repo.comment(n, f'@{actor} ' + '\n\n'.join(replies) + '\n\n<sub>Steward (automated)</sub>')
        self.memo['cursor'] = newest

    def run_command(self, cmd, args, login):
        """Same commands from Discord (login = linked GitHub account)."""
        if cmd == 'next':
            return self.next_text()
        if cmd == 'status':
            return self.status_text()
        r = commands.run(cmd, args, login, self.state, self.cfg, self.sizes, time.time(), self.is_maintainer(login))
        self.save()
        return r

    def link_code(self, discord_id):
        code = secrets.token_hex(4)
        self.memo['link_codes'][code] = discord_id
        self.save()
        return code

    # -- claims ----------------------------------------------------------
    def sweep(self, events):
        expired, warn = self.state.sweep(self.cfg.policy, time.time())
        warned = set(self.memo.setdefault('warned', []))
        for c in expired:
            events.append({'kind': 'claim_expired', 'text': f'claim #{c.id} by @{c.user} ({c.label()}) lapsed'})
        for c in warn:
            if str(c.id) not in warned:
                warned.add(str(c.id))
                events.append({'kind': 'claim_warn', 'login': c.user,
                               'text': f'@{c.user}: claim #{c.id} ({c.label()}) lapses in '
                                       f'{self.cfg.policy.claim_lapse_days - self.cfg.policy.claim_warn_days} days without activity'})
        self.memo['warned'] = sorted(warned & {str(c.id) for c in self.state.claims})
        if self.memo.get('claims_issue') is None:
            self.memo['claims_issue'] = self.repo.find_issue(CLAIMS_MARK, 'Active claims')
        body = report.claims_issue(self.state)
        if body != self.memo.get('claims_body'):
            self.repo.edit_issue(self.memo['claims_issue'], body)
            self.memo['claims_body'] = body

    # -- roadmap ---------------------------------------------------------
    def done_units(self):
        return {u for u in (self.cfg.unit_of(t['path']) for t in self.repo.tree(self.branch) if t['type'] == 'blob')
                if isinstance(u, int)}

    def next_text(self, n=8):
        rows = roadmap.load_table(self.cfg)
        done = self.done_units()
        sug = roadmap.suggest(rows, done, lambda a, s: self.state.holder(a, s) is not None, n)
        if not sug:
            return 'nothing unclaimed left - nice.'
        return 'Unclaimed functions to try (claim with `/claim 0xADDR`):\n' + '\n'.join(
            f'- `0x{a:08X}` {size} bytes{" (next to finished work)" if near else ""}' for _, a, size, _, near in sug)

    def status_text(self):
        rows = roadmap.load_table(self.cfg)
        s = roadmap.stats(rows, self.done_units())
        return (f'{s["done_functions"]}/{s["functions"]} functions, {s["done_bytes"]}/{s["bytes"]} bytes '
                f'({s["percent"]:.1f}%), {len(self.state.claims)} active claims')

    # -- pull requests ---------------------------------------------------
    def merged_count(self, author, number):
        key = f'{author}:{number}'
        if key not in self.memo['merged_counts']:
            self.memo['merged_counts'][key] = self.repo.merged_count(author, number)
        return self.memo['merged_counts'][key]

    def process_pr(self, p, events):
        n, head = p['number'], p['head']['sha']
        author = p['user']['login']
        files = self.repo.pr_files(n)
        pr = PR(n, author, head, [f['filename'] for f in files], p.get('body') or '', p['draft'],
                self.merged_count(author, n), self.is_maintainer(author),
                [f['filename'] for f in files if f['status'] == 'added'])
        m = self.memo['prs'].setdefault(str(n), {})
        if m.get('head') != head:
            m.clear()
            m['head'] = head
        status, art, run = self.repo.gate_artifact(head)
        gates = from_artifact(self.cfg, art, head) if status == 'done' else []
        if self.judge is not None and self.judge.judge_gates(self.cfg) and not pr.draft:
            if 'judge' not in m:   # once per head: the oracle is deterministic
                m['judge'] = judge_runner.to_json(self.judge.run(self.repo, self.cfg, self.branch, n, head, files))
            gates += judge_runner.from_json(m['judge'])
            if all(g.where == 'judge' for g in self.cfg.gates):
                status = 'done'
        d = decide(pr, gates, self.state, self.cfg, self.sizes, time.time())
        tri = rev = None
        if self.use_ai:
            if 'triage' not in m:
                out, meta = roles.make('triage', self.cfg).run(p['title'], pr.body, pr.files, author)
                m['triage'] = (out.model_dump() if out else None, meta)
            if status == 'done' and 'review' not in m:
                diff = '\n'.join(f'--- {f["filename"]} ({f["status"]})\n{f.get("patch", "")}' for f in files)
                out, meta = roles.make('review', self.cfg).run(d, gates, diff, pr.body)
                m['review'] = (out.model_dump() if out else None, meta)
            if m.get('triage'):
                tri = roles.Triage(**m['triage'][0]) if m['triage'][0] else None
                if tri is None:     # the model failed or refused: a human looks
                    tri = roles.Triage(kind='other', ai_assistance='unstated', hostile_content=False, needs_human=True,
                                       human_reason='triage produced no answer', summary='')
            if m.get('review') and m['review'][0]:
                rev = roles.Review(**m['review'][0])

        prog = self.progress(d.units)
        m['progress'] = prog
        action, acts = actions.plan(pr, d, gates, tri, rev, progress=prog)
        actions.validate(acts, d, pr)
        sig = hashlib.sha256(json.dumps([[a.kind, a.args] for a in acts], sort_keys=True, default=str).encode()).hexdigest()
        if m.get('sig') == sig and action != 'merge':
            return
        model = {k: m[k][1] for k in ('triage', 'review') if m.get(k)}
        audit.append(self.audit_path, pr, d, gates, self.cfg, model=model or None)
        for a in acts:
            self.execute(a, pr, p, d, events)
        m['sig'] = sig
        m['action'] = action

    def execute(self, a, pr, p, d, events):
        n = pr.number
        if a.kind == 'add_labels':
            self.repo.set_labels(n, a.args['labels'])
        elif a.kind == 'upsert_report':
            self.repo.upsert_comment(n, a.args['marker'], a.args['body'])
        elif a.kind == 'ask_owner':
            key = f'{n}:{a.args["owner"]}'
            if key not in self.state.asks:
                self.state.asks[key] = time.time()
                self.repo.comment(n, f'@{a.args["owner"]} this PR by @{pr.author} changes function(s) you wrote. '
                                     f'Reply `/consent {n}` to let it proceed (your credit stays). After '
                                     f'{self.cfg.policy.owner_rule_days} days without an answer it may go ahead.'
                                     '\n\n<sub>Steward (automated)</sub>')
        elif a.kind == 'escalate':
            m = self.memo['prs'][str(n)]
            if not m.get('escalated'):
                m['escalated'] = True
                who = ' '.join(f'@{x}' for x in self.cfg.policy.maintainers) or 'a maintainer'
                self.repo.comment(n, f'{who} this needs a human: ' + '; '.join(a.args['reasons'])[:900]
                                     + '\n\n<sub>Steward (automated)</sub>')
                events.append({'kind': 'escalated', 'pr': n, 'url': p['html_url'], 'text': '; '.join(a.args['reasons'])[:300]})
        elif a.kind == 'enqueue_merge':
            self.merge(pr, p, d, events)

    def merge(self, pr, p, d, events):
        fresh = self.repo.pr(pr.number)
        if fresh['state'] != 'open' or fresh['head']['sha'] != pr.head_sha:
            return
        if self.repo.behind_by(fresh['base']['ref'], pr.head_sha) > 0:
            # gates ran against an old base: bring the branch up to date; CI reruns on the new head
            self.repo.update_branch(pr.number, pr.head_sha)
            self.log(f'#{pr.number}: behind base, branch updated')
            return
        self.repo.merge(pr.number, pr.head_sha, self.cfg.policy.merge_method,
                        title=f'{fresh["title"]} (#{pr.number})')
        ints = [u for u in d.units if isinstance(u, int)]
        self.state.record_merge(pr.author, d.units)
        for u in d.units:   # a merged function's claim is fulfilled
            c = self.state.holder(u, self.sizes.get(u, 1)) if isinstance(u, int) else self.state.holder(u)
            if c and c.user == pr.author:
                self.state.claims.remove(c)
        self.memo['merged_counts'] = {}
        prog = self.memo['prs'].get(str(pr.number), {}).get('progress') or {}
        text = f'#{pr.number} by @{pr.author} merged: ' + ', '.join(f'0x{u:08X}' for u in ints)
        if prog.get('total'):
            after = prog['before'] + prog['new']
            text += (f'
{prog["new"]} new, the project is now at {after:,} / {prog["total"]:,} functions matched '
                     f'({100.0 * after / prog["total"]:.2f}%)')
        self._base_units = None     # the base branch moved
        events.append({'kind': 'merged', 'pr': pr.number, 'url': p['html_url'], 'author': pr.author, 'text': text})
        self.log(f'#{pr.number}: merged')

    def progress(self, units):
        """Matched units on the base branch, and how many of this PR's units are new, against the function table."""
        if getattr(self, '_base_units', None) is None:
            self._base_units = {u for u in (self.cfg.unit_of(t['path']) for t in self.repo.tree(self.branch)
                                            if t['type'] == 'blob') if u is not None}
        base = self._base_units
        new = [u for u in units if u not in base]
        return {'before': len(base), 'new': len(new), 'total': len(self.sizes),
                'bytes_before': sum(self.sizes.get(u, 0) for u in base),
                'bytes_new': sum(self.sizes.get(u, 0) for u in new), 'bytes_total': sum(self.sizes.values())}

    # -- tick ------------------------------------------------------------
    def tick(self):
        events = []
        self.refresh_config()
        self.repo.ensure_labels(LABEL_COLORS) if not self.memo.get('labels_ok') else None
        self.memo['labels_ok'] = True
        self.bootstrap_owners()
        self.poll_commands(events)
        self.sweep(events)
        for p in self.repo.open_prs():
            try:
                self.process_pr(p, events)
            except GitHubError as e:
                self.log(f'#{p["number"]}: {e}')
        self.save()
        return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--app', required=True, help='GitHub App slug (credentials in ~/.decompathon/<slug>)')
    ap.add_argument('--data', help='default ~/.decompathon/data/<owner>_<name>')
    ap.add_argument('--interval', type=int, default=60)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--no-ai', action='store_true')
    ap.add_argument('--judge-workspace', help='dedicated local checkout where where=judge gates run (game files in place)')
    a = ap.parse_args()
    data = a.data or os.path.join(os.path.expanduser('~'), '.decompathon', 'data', a.repo.replace('/', '_'))
    judge = judge_runner.JudgeRunner(a.judge_workspace) if a.judge_workspace else None
    s = Steward(Repo(App(a.app), a.repo), data, use_ai=not a.no_ai, judge=judge)
    while True:
        try:
            for e in s.tick():
                print(f'[{e["kind"]}] {e.get("text", "")}', flush=True)
        except Exception:
            traceback.print_exc()
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == '__main__':
    main()
