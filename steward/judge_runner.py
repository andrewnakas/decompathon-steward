"""Run `where = "judge"` command gates on this machine (the judge box), for projects whose oracle needs files
that may never leave it (the owner's ROM, unpacked game code).

The workspace is a dedicated checkout of the project, prepared once by the owner (game files in place, generated
inputs built). For each PR head it is reset to the newest base branch, only the PR's unit files (unit_paths) are
copied in from the PR head, the judge gates run from the base's own scripts, and the unit paths are reset again.
Everything else the PR changes is ignored here: protected paths never reach a gate.
"""
import base64
import dataclasses
import os
import subprocess

from . import gates as gate_mod
from .decider import GateResult


class JudgeRunner:
    def __init__(self, workspace, log=print, remote=None):
        self.ws = os.path.abspath(workspace)
        self.remote = remote        # tests: a local path instead of https://github.com/<repo>.git
        if not os.path.isdir(os.path.join(self.ws, '.git')):
            raise SystemExit(f'judge workspace {self.ws} is not a git checkout')
        self.log = log

    def judge_gates(self, cfg):
        return [g for g in cfg.gates if g.where == 'judge' and not g.run.startswith('artifact:')]

    def _git(self, *args, token=None, repo=None):
        env = dict(os.environ)
        if token:   # auth only through the environment: never in argv, the remote config or a log line
            basic = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
            env.update({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraheader',
                        'GIT_CONFIG_VALUE_0': f'Authorization: Basic {basic}', 'GIT_TERMINAL_PROMPT': '0'})
        p = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=self.ws, env=env,
                           capture_output=True, text=True, errors='replace')
        if p.returncode != 0:
            raise RuntimeError(f'git {args[0]} failed: {p.stderr.strip()[-300:]}')
        return p.stdout

    def _unit_dirs(self, cfg):
        out = []
        for pat in cfg.unit_paths:
            d = pat.split('*', 1)[0].rstrip('/')
            if not d or os.path.isabs(d) or '..' in d.split('/'):
                raise RuntimeError(f'unit path {pat!r} has no safe directory prefix')
            out.append(d)
        return out

    def _reset(self, cfg, ref):
        self._git('checkout', '--force', '--detach', ref)
        for d in self._unit_dirs(cfg):
            self._git('clean', '-fdq', '--', d)

    def run(self, repo, cfg, branch, pr_number, head_sha, files):
        """-> [GateResult] for every judge gate. `files` = the PR's file list from the API."""
        gs = self.judge_gates(cfg)
        if not gs:
            return []
        tok = repo.app.token(repo.repo)
        self._git('fetch', '--quiet', self.remote or f'https://github.com/{repo.repo}.git', branch, token=tok)
        base = self._git('rev-parse', 'FETCH_HEAD').strip()
        try:
            self._reset(cfg, base)
            for f in files:
                for path, gone in ((f.get('previous_filename'), True), (f['filename'], f['status'] == 'removed')):
                    if not path or cfg.unit_of(path) is None:
                        continue
                    dst = os.path.join(self.ws, *path.split('/'))
                    if not os.path.realpath(dst).startswith(self.ws + os.sep):
                        continue
                    if gone:
                        if os.path.isfile(dst):
                            os.remove(dst)
                    else:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(dst, 'w', encoding='utf-8', newline='\n') as out:
                            out.write(repo.file_text(path, head_sha))
            results = []
            for g in gs:
                self.log(f'#{pr_number}: judge gate {g.name}')
                results.append(gate_mod.run(g, self.ws, head_sha, pr_number))
            return results
        except RuntimeError as e:
            return [GateResult(g.name, 'error', str(e)[:200], head_sha) for g in gs]
        finally:
            try:
                self._reset(cfg, base)
            except RuntimeError as e:
                self.log(f'judge workspace reset failed: {e}')


def to_json(results):
    return [dataclasses.asdict(r) for r in results]


def from_json(rows):
    return [GateResult(**r) for r in rows]
