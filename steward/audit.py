"""Append-only, hash-chained decision log (audit.jsonl).

Every entry carries the hash of the one before it, so a deleted or edited line breaks verify().
"""
import hashlib
import json
import os
import time
from dataclasses import asdict

from .config import unit_label

GENESIS = '0' * 64


def _hash(entry):
    body = {k: v for k, v in entry.items() if k != 'hash'}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def last_hash(path):
    if not os.path.exists(path):
        return GENESIS
    last = GENESIS
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                last = json.loads(line)['hash']
    return last


def append(path, pr, decision, gates, cfg, model=None, now=None):
    entry = {
        'ts': now if now is not None else time.time(),
        'project': cfg.name,
        'pr': pr.number,
        'author': pr.author,
        'head_sha': pr.head_sha,
        'action': decision.action,
        'units': [unit_label(u) for u in decision.units],
        'reasons': [asdict(r) for r in decision.reasons],
        'gates': [{'name': g.name, 'status': g.status, 'head_sha': g.head_sha,
                   'output_sha256': g.output_sha256} for g in gates],
        'config_sha256': cfg.digest,
        'model': model,             # {'id', 'prompt_version'} when an agent's output was shown, else None
        'prev': last_hash(path),
    }
    entry['hash'] = _hash(entry)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def verify(path):
    """-> (ok, n_entries, first_bad_line or None)"""
    prev = GENESIS
    n = 0
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get('prev') != prev or _hash(e) != e.get('hash'):
                return False, n, i
            prev = e['hash']
            n += 1
    return True, n, None
