import os
import subprocess
import sys

from steward import config
from steward.judge_runner import JudgeRunner

TOML = '''[project]
name = "t"
repo = "o/t"
unit_paths = ["src/funcs/**"]
unit_regex = 'func_([0-9A-F]{8})\\.c$'

[policy]
maintainers = ["owner"]

[[gates]]
name = "match"
run = "python tools/gate.py"
where = "judge"
'''

# the base's gate: lists what it sees and fails if tools/ was tampered with
GATE = '''import glob, os, sys
print("SEEN " + " ".join(sorted(os.path.basename(f) for f in glob.glob("src/funcs/*.c"))))
print("TOOLS " + open("tools/marker.txt").read().strip())
sys.exit(0)
'''


def git(cwd, *a):
    subprocess.run(['git', '-c', 'safe.directory=*', '-c', 'user.name=t', '-c', 'user.email=t@t', *a], cwd=cwd,
                   check=True, capture_output=True)


class FakeApp:
    def token(self, repo):
        return 'not-a-token'


class FakeRepo:
    def __init__(self, files):
        self.app, self.repo, self.files = FakeApp(), 'o/t', files

    def file_text(self, path, ref):
        return self.files[path]


def setup(tmp_path):
    origin = tmp_path / 'origin'
    (origin / 'src' / 'funcs').mkdir(parents=True)
    (origin / 'tools').mkdir()
    (origin / 'tools' / 'gate.py').write_text(GATE)
    (origin / 'tools' / 'marker.txt').write_text('base')
    (origin / 'src' / 'funcs' / 'func_80000100.c').write_text('int a;\n')
    (origin / 'steward.toml').write_text(TOML)
    git(origin, 'init', '-q', '-b', 'main')
    git(origin, 'add', '.')
    git(origin, 'commit', '-qm', 'base')
    ws = tmp_path / 'ws'
    git(tmp_path, 'clone', '-q', str(origin), str(ws))
    return origin, ws, config.load(str(origin / 'steward.toml'))


def test_pr_units_reach_gate_protected_paths_do_not(tmp_path):
    origin, ws, cfg = setup(tmp_path)
    files = [{'filename': 'src/funcs/func_80000200.c', 'status': 'added'},
             {'filename': 'src/funcs/func_80000100.c', 'status': 'removed'},
             {'filename': 'tools/marker.txt', 'status': 'modified'}]
    repo = FakeRepo({'src/funcs/func_80000200.c': 'int b;\n', 'tools/marker.txt': 'HACKED'})
    r = JudgeRunner(str(ws), log=lambda *_: None, remote=str(origin)).run(repo, cfg, 'main', 1, 'abc', files)
    assert len(r) == 1 and r[0].status == 'pass'
    tail = r[0].details['tail']
    assert 'SEEN func_80000200.c' in tail          # added unit in, removed unit gone
    assert 'TOOLS base' in tail                    # the PR's edit to the gate's inputs never landed
    # workspace is back to the base afterwards
    assert sorted(os.listdir(ws / 'src' / 'funcs')) == ['func_80000100.c']


def test_unsafe_paths_are_skipped(tmp_path):
    origin, ws, cfg = setup(tmp_path)
    files = [{'filename': 'src/funcs/../../evil/func_80000300.c', 'status': 'added'}]
    repo = FakeRepo({'src/funcs/../../evil/func_80000300.c': 'x'})
    JudgeRunner(str(ws), log=lambda *_: None, remote=str(origin)).run(repo, cfg, 'main', 2, 'abc', files)
    assert not (tmp_path / 'evil').exists()


def test_service_modules_import():
    import steward.bot  # noqa: F401  (the daemon and bot have no other tests that import them)
    import steward.daemon  # noqa: F401
