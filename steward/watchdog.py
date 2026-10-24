"""Keep the Steward bot running: restart it when it exits or stops making progress.

  python -m steward.watchdog [--stale SECONDS] [--log-dir DIR] [--module steward.daemon] -- <bot/daemon arguments>

The bot beats a heartbeat file (steward.heartbeat) at every tick, PR and gate start/end. A gate can legitimately run
for up to its timeout, after which the gate kills its own process tree; the watchdog is the backstop for a hang
anywhere else (a wedged network call, a kill that did not take). If no beat arrives for --stale seconds (default
1.2 x the longest gate timeout + 10 min), the bot's whole process tree is killed and it is started again.
Restarts back off (30 s doubling to 10 min) so a bot that crashes at start-up does not spin.
"""
import argparse
import datetime
import os
import subprocess
import sys
import time

from . import heartbeat
from .gates import kill_tree

ABOVE_NORMAL_PRIORITY_CLASS = 0x8000
CREATE_NO_WINDOW = 0x08000000


def log(f, msg):
    f.write(f'[watchdog {datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n')
    f.flush()


def default_stale():
    from .config import Gate
    return int(1.2 * Gate.__dataclass_fields__['timeout'].default) + 600


def start(bot_args, hb, out, err, module='steward.bot'):
    env = dict(os.environ, STEWARD_HEARTBEAT=hb, PYTHONUNBUFFERED='1')
    heartbeat_reset(hb)
    kw = {}
    if os.name == 'nt':   # set at creation: the gates' compilers inherit it (SetPriorityClass later did not stick)
        kw['creationflags'] = ABOVE_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    else:
        kw['start_new_session'] = True
    extra = ['--normal-priority'] if module == 'steward.bot' else []
    return subprocess.Popen([sys.executable, '-u', '-m', module, *extra, *bot_args],
                            stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=env, **kw)


def heartbeat_reset(hb):
    """A fresh start counts as a beat, so start-up time is not mistaken for a hang."""
    os.environ['STEWARD_HEARTBEAT'] = hb
    heartbeat.beat('started by watchdog')


def watch(bot_args, hb, out, err, stale, poll=30, max_restarts=None, sleep=time.sleep, module='steward.bot'):
    backoff, restarts = 30, 0
    while True:
        p = start(bot_args, hb, out, err, module)
        log(out, f'bot started (pid {p.pid})')
        began = time.time()
        while True:
            sleep(poll)
            code = p.poll()
            if code is not None:
                log(out, f'bot exited with code {code}')
                break
            a = heartbeat.age(hb)
            if a is not None and a > stale:
                log(out, f'no heartbeat for {int(a)}s (limit {stale}s): killing pid {p.pid} and its children')
                kill_tree(p)
                try:
                    p.wait(60)
                except subprocess.TimeoutExpired:
                    pass
                break
        restarts += 1
        if max_restarts is not None and restarts >= max_restarts:
            return restarts
        if time.time() - began > 3600:
            backoff = 30   # it ran fine for a while: this is a fresh failure
        log(out, f'restarting in {backoff}s')
        sleep(backoff)
        backoff = min(backoff * 2, 600)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument('--stale', type=int, default=None, help='seconds without a heartbeat before a restart')
    ap.add_argument('--log-dir', default=os.path.join(os.path.expanduser('~'), '.decompathon'))
    ap.add_argument('--module', default='steward.bot', choices=['steward.bot', 'steward.daemon'],
                    help='steward.daemon: GitHub only, no Discord (one log dir per project)')
    ap.add_argument('bot_args', nargs=argparse.REMAINDER)
    a = ap.parse_args()
    bot_args = a.bot_args[1:] if a.bot_args[:1] == ['--'] else a.bot_args
    os.makedirs(a.log_dir, exist_ok=True)
    hb = os.path.join(a.log_dir, 'bot.heartbeat')
    out = open(os.path.join(a.log_dir, 'bot.log'), 'a', buffering=1, encoding='utf-8', errors='replace')
    err = open(os.path.join(a.log_dir, 'bot.err.log'), 'a', buffering=1, encoding='utf-8', errors='replace')
    watch(bot_args, hb, out, err, a.stale or default_stale(), module=a.module)


if __name__ == '__main__':
    main()
