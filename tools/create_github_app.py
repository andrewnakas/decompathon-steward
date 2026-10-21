#!/usr/bin/env python3
"""Register Steward's GitHub App with GitHub's manifest flow (one click in a signed-in browser).

  python tools/create_github_app.py --name "Decompathon Steward Test" --repo-url https://github.com/o/r

Serves http://127.0.0.1:8765/ : a page that posts the manifest to GitHub. After "Create GitHub App",
GitHub redirects to /callback?code=..., and this script trades the code for the app's credentials.
Secrets go to ~/.decompathon/<slug>/ (never printed, never in a repo):
  app.json (id, slug, client_id, html_url), private-key.pem, secrets.json (client/webhook secrets)
The app has no webhook (Steward polls), is private, and asks only for what Steward uses.
"""
import argparse
import html
import http.server
import json
import os
import threading
import urllib.parse

import requests

PERMISSIONS = {
    'contents': 'write',        # merge PRs
    'pull_requests': 'write',   # labels, review state, comments on PRs
    'issues': 'write',          # claims issue, comments, labels
    'checks': 'read',
    'statuses': 'read',
    'actions': 'read',          # read gate artifacts from workflow runs
    'metadata': 'read',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--repo-url', required=True)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--wait', type=int, default=3600, help='seconds to wait for the browser')
    a = ap.parse_args()
    base = f'http://127.0.0.1:{a.port}'
    manifest = {
        'name': a.name,
        'url': a.repo_url,
        'description': 'Decompathon Steward: checks, explains and merges decomp PRs with deterministic gates.',
        'hook_attributes': {'url': 'https://example.invalid/steward-hook', 'active': False},
        'redirect_url': base + '/callback',
        'public': False,
        'default_permissions': PERMISSIONS,
        'default_events': [],
    }
    done = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == '/':
                self._send(
                    '<!doctype html><title>Create Steward app</title><body>'
                    '<form id="f" method="post" action="https://github.com/settings/apps/new">'
                    f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest))}">'
                    '<button type="submit">Continue to GitHub</button></form>'
                    '<script>document.getElementById("f").submit()</script>')
                return
            if u.path == '/callback':
                code = urllib.parse.parse_qs(u.query).get('code', [''])[0]
                r = requests.post(f'https://api.github.com/app-manifests/{code}/conversions',
                                  headers={'Accept': 'application/vnd.github+json'}, timeout=30)
                if r.status_code != 201:
                    self._send(f'<p>Conversion failed: HTTP {r.status_code}</p>')
                    print(f'conversion failed: HTTP {r.status_code} {r.text[:200]}')
                    done.set()
                    return
                d = r.json()
                out = os.path.join(os.path.expanduser('~'), '.decompathon', d['slug'])
                os.makedirs(out, exist_ok=True)
                with open(os.path.join(out, 'private-key.pem'), 'w') as f:
                    f.write(d['pem'])
                with open(os.path.join(out, 'secrets.json'), 'w') as f:
                    json.dump({k: d.get(k) for k in ('client_secret', 'webhook_secret')}, f)
                info = {k: d.get(k) for k in ('id', 'slug', 'client_id', 'html_url', 'name')}
                with open(os.path.join(out, 'app.json'), 'w') as f:
                    json.dump(info, f, indent=1)
                self._send(f'<p>Created <b>{html.escape(d["name"])}</b>. Install it: '
                           f'<a href="{d["html_url"]}/installations/new">{d["html_url"]}/installations/new</a></p>')
                print(f'created app id={info["id"]} slug={info["slug"]} -> {out}')
                done.set()

    srv = http.server.HTTPServer(('127.0.0.1', a.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'open {base}/ in a browser signed in to GitHub', flush=True)
    done.wait(timeout=a.wait)
    srv.shutdown()


if __name__ == '__main__':
    main()
