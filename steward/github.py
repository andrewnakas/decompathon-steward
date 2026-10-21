"""GitHub as Steward's GitHub App: installation tokens, REST calls, gate artifacts.

Credentials live in ~/.decompathon/<app slug>/ (written by tools/create_github_app.py).
"""
import base64
import io
import json
import os
import time
import zipfile

import jwt
import requests

API = 'https://api.github.com'
UA = 'decompathon-steward'


class GitHubError(RuntimeError):
    pass


class App:
    def __init__(self, slug, home=None):
        self.dir = os.path.join(home or os.path.join(os.path.expanduser('~'), '.decompathon'), slug)
        self.info = json.load(open(os.path.join(self.dir, 'app.json')))
        self.key = open(os.path.join(self.dir, 'private-key.pem')).read()
        self._tokens = {}

    def jwt(self):
        now = int(time.time())
        return jwt.encode({'iat': now - 60, 'exp': now + 540, 'iss': str(self.info['id'])}, self.key, algorithm='RS256')

    def token(self, repo):
        tok, exp = self._tokens.get(repo, (None, 0))
        if tok and exp - time.time() > 120:
            return tok
        h = {'Authorization': f'Bearer {self.jwt()}', 'Accept': 'application/vnd.github+json', 'User-Agent': UA}
        r = requests.get(f'{API}/repos/{repo}/installation', headers=h, timeout=30)
        if r.status_code != 200:
            raise GitHubError(f'app is not installed on {repo} (HTTP {r.status_code})')
        r = requests.post(f'{API}/app/installations/{r.json()["id"]}/access_tokens', headers=h, timeout=30)
        if r.status_code != 201:
            raise GitHubError(f'installation token: HTTP {r.status_code}')
        d = r.json()
        exp = time.mktime(time.strptime(d['expires_at'], '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
        self._tokens[repo] = (d['token'], exp)
        return d['token']

    @property
    def login(self):
        return f'{self.info["slug"]}[bot]'


class Repo:
    def __init__(self, app, repo):
        self.app, self.repo = app, repo

    def _req(self, method, path, ok=(200,), raw=False, **kw):
        url = path if path.startswith('http') else f'{API}/repos/{self.repo}{path}'
        h = {'Authorization': f'token {self.app.token(self.repo)}', 'Accept': 'application/vnd.github+json',
             'User-Agent': UA, 'X-GitHub-Api-Version': '2022-11-28'}
        r = requests.request(method, url, headers=h, timeout=60, **kw)
        if r.status_code not in ok:
            raise GitHubError(f'{method} {path}: HTTP {r.status_code} {r.text[:200]}')
        if raw:
            return r
        return r.json() if r.content else None

    def get(self, path, **params):
        return self._req('GET', path, params=params)

    def paged(self, path, **params):
        params.setdefault('per_page', 100)
        page = 1
        while True:
            items = self._req('GET', path, params=dict(params, page=page))
            yield from items
            if len(items) < params['per_page']:
                return
            page += 1

    # -- pull requests -----------------------------------------------------
    def open_prs(self):
        return list(self.paged('/pulls', state='open'))

    def pr(self, n):
        return self.get(f'/pulls/{n}')

    def pr_files(self, n):
        return list(self.paged(f'/pulls/{n}/files'))

    def merged_count(self, author, before_number):
        n = 0
        for p in self.paged('/pulls', state='closed'):
            if p['merged_at'] and p['user']['login'] == author and p['number'] != before_number:
                n += 1
        return n

    def behind_by(self, base, head_sha):
        return self.get(f'/compare/{base}...{head_sha}')['behind_by']

    def update_branch(self, n, head_sha):
        return self._req('PUT', f'/pulls/{n}/update-branch', ok=(202,), json={'expected_head_sha': head_sha})

    def merge(self, n, head_sha, method='merge', title=None):
        body = {'sha': head_sha, 'merge_method': method}
        if title:
            body['commit_title'] = title
        return self._req('PUT', f'/pulls/{n}/merge', json=body)

    # -- issues, labels, comments -----------------------------------------
    def ensure_labels(self, labels):
        have = {l['name'] for l in self.paged('/labels')}
        for name, color in labels.items():
            if name not in have:
                self._req('POST', '/labels', ok=(201,), json={'name': name, 'color': color})

    def set_labels(self, n, add, managed_prefix='steward:'):
        current = {l['name'] for l in self.get(f'/issues/{n}')['labels']}
        keep = {l for l in current if not l.startswith(managed_prefix)}
        want = sorted(keep | set(add))
        if set(want) != current:
            self._req('PUT', f'/issues/{n}/labels', json={'labels': want})

    def comment(self, n, body):
        return self._req('POST', f'/issues/{n}/comments', ok=(201,), json={'body': body})

    def upsert_comment(self, n, marker, body):
        """Edit Steward's own marked comment, or post it. -> True if anything changed."""
        for c in self.paged(f'/issues/{n}/comments'):
            if c['user']['login'] == self.app.login and marker in c['body']:
                if c['body'] != body:
                    self._req('PATCH', f'/issues/comments/{c["id"]}', json={'body': body})
                    return True
                return False
        self.comment(n, body)
        return True

    def comments_since(self, since_iso):
        return list(self.paged('/issues/comments', since=since_iso, sort='created', direction='asc'))

    def find_issue(self, marker, title):
        for i in self.paged('/issues', state='open', creator=self.app.login):
            if marker in (i.get('body') or ''):
                return i['number']
        return self._req('POST', '/issues', ok=(201,), json={'title': title, 'body': marker})['number']

    def edit_issue(self, n, body):
        self._req('PATCH', f'/issues/{n}', json={'body': body})

    # -- contents ----------------------------------------------------------
    def tree(self, ref):
        return self.get(f'/git/trees/{ref}', recursive=1)['tree']

    def file_text(self, path, ref):
        d = self.get(f'/contents/{path}', ref=ref)
        return base64.b64decode(d['content']).decode('utf-8', 'replace')

    # -- gate artifacts ----------------------------------------------------
    def gate_artifact(self, head_sha, workflow='gates', artifact='steward-gates'):
        """-> ('pending'|'missing'|'done', {filename: text}, run). Newest run of `workflow` for this head."""
        runs = self.get('/actions/runs', head_sha=head_sha, per_page=20)['workflow_runs']
        runs = [r for r in runs if r['name'] == workflow]
        if not runs:
            return 'missing', {}, None
        run = max(runs, key=lambda r: r['run_number'])
        if run['status'] != 'completed':
            return 'pending', {}, run
        arts = self.get(f'/actions/runs/{run["id"]}/artifacts')['artifacts']
        art = next((x for x in arts if x['name'] == artifact and not x['expired']), None)
        if not art:
            return 'missing', {}, run
        z = self._req('GET', art['archive_download_url'], raw=True)
        files = {}
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            for name in zf.namelist():
                files[name] = zf.read(name).decode('utf-8', 'replace')
        return 'done', files, run
