"""A heartbeat file the watchdog reads: Steward touches it whenever it makes progress (each tick, each gate start/end).

The path comes from STEWARD_HEARTBEAT (set by steward.watchdog); without it, beat() does nothing."""
import json
import os
import time


def beat(what=''):
    path = os.environ.get('STEWARD_HEARTBEAT')
    if not path:
        return
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump({'t': time.time(), 'pid': os.getpid(), 'what': what[:200]}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def age(path):
    """Seconds since the last beat, or None if there is none."""
    try:
        with open(path) as f:
            return time.time() - float(json.load(f)['t'])
    except (OSError, ValueError, KeyError):
        return None
