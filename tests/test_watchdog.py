import os
import sys
import time

from steward import gates, heartbeat, watchdog
from steward.config import Gate


def test_gate_timeout_kills_grandchildren(tmp_path):
    # the shell's child keeps the pipes open: killing only the shell used to hang the bot for good
    (tmp_path / 'sleep.py').write_text('import time\ntime.sleep(120)\n')
    (tmp_path / 'parent.py').write_text('import subprocess, sys\n'
                                        'subprocess.run([sys.executable, "sleep.py"])\n')
    g = Gate('slow', f'"{sys.executable}" parent.py', timeout=3)
    t = time.time()
    r = gates.run(g, str(tmp_path), 'abc')
    assert r.status == 'error' and 'timed out' in r.summary
    assert time.time() - t < 60


def test_heartbeat_age(tmp_path, monkeypatch):
    hb = str(tmp_path / 'hb')
    assert heartbeat.age(hb) is None
    monkeypatch.setenv('STEWARD_HEARTBEAT', hb)
    heartbeat.beat('x')
    assert heartbeat.age(hb) < 5


def test_watchdog_restarts_stale_bot(tmp_path, monkeypatch):
    hb = str(tmp_path / 'hb')
    started = []

    class P:
        pid = 0

        def poll(self):
            return None

        def wait(self, t=None):
            return 0

    def fake_start(args, hb_, out, err):
        started.append(args)
        os.environ['STEWARD_HEARTBEAT'] = hb_
        heartbeat.beat('start')
        return P()

    killed = []
    monkeypatch.setattr(watchdog, 'start', fake_start)
    monkeypatch.setattr(watchdog, 'kill_tree', lambda p: killed.append(p))
    monkeypatch.setattr(heartbeat, 'age', lambda path: 10_000)   # never beats again
    out = open(tmp_path / 'log', 'w')
    n = watchdog.watch(['--repo', 'x'], hb, out, out, stale=100, poll=0, max_restarts=2, sleep=lambda s: None)
    assert n == 2 and len(started) == 2 and len(killed) == 2
    out.close()
    assert 'no heartbeat' in open(tmp_path / 'log').read()


def test_watchdog_restarts_exited_bot(tmp_path, monkeypatch):
    class P:
        pid = 0

        def poll(self):
            return 1

    monkeypatch.setattr(watchdog, 'start', lambda *a: P())
    out = open(tmp_path / 'log', 'w')
    assert watchdog.watch([], str(tmp_path / 'hb'), out, out, stale=100, poll=0, max_restarts=3,
                          sleep=lambda s: None) == 3


def test_default_stale_covers_gate_timeout():
    assert watchdog.default_stale() > Gate('g', 'x').timeout
