"""Claims, owners and owner-rule consents. One JSON file, written only by the Steward App.

Rules (from halo2-decompiled #9):
- a claim is one file or a retail range up to policy.claim_max_bytes; at most claim_max_open per person;
- a claim lapses after claim_lapse_days without activity (warned at claim_warn_days);
- a merged unit belongs to its author; changing it needs their consent or owner_rule_days of silence.
"""
import json
import os
from dataclasses import asdict, dataclass, field

DAY = 86400


class ClaimError(ValueError):
    pass


@dataclass
class Claim:
    id: int
    user: str
    start: int = 0              # retail range [start, end)
    end: int = 0
    path: str = ''              # or a file
    created: float = 0
    last_activity: float = 0
    source: str = ''            # issue/discord link

    def covers(self, addr, size=1, path=''):
        if self.path:
            return bool(path) and path == self.path
        return addr < self.end and self.start < addr + max(size, 1)

    def label(self):
        return self.path or f'0x{self.start:08X}-0x{self.end:08X}'


@dataclass
class State:
    claims: list = field(default_factory=list)
    owners: dict = field(default_factory=dict)      # 'XXXXXXXX' -> author of the merged unit
    asks: dict = field(default_factory=dict)        # 'pr:owner' -> time the consent ask was posted
    consents: dict = field(default_factory=dict)    # 'pr:owner' -> time consent was given
    next_id: int = 1

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return cls()
        d = json.load(open(path, encoding='utf-8'))
        d['claims'] = [Claim(**c) for c in d.get('claims', [])]
        return cls(**d)

    def save(self, path):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=1, sort_keys=True)
        os.replace(tmp, path)

    # -- claims ------------------------------------------------------------
    def claim(self, user, policy, now, start=0, size=0, path='', source=''):
        if not path and size <= 0:
            raise ClaimError('claim a file or a range with a size')
        if size > policy.claim_max_bytes:
            raise ClaimError(f'a claim may cover at most 0x{policy.claim_max_bytes:X} bytes')
        mine = [c for c in self.claims if c.user == user]
        if len(mine) >= policy.claim_max_open:
            raise ClaimError(f'{user} already holds {len(mine)} claims (max {policy.claim_max_open})')
        new = Claim(self.next_id, user, start, start + size, path, now, now, source)
        for c in self.claims:
            if (path and c.path == path) or (not path and not c.path and c.covers(start, size)):
                raise ClaimError(f'overlaps claim #{c.id} by {c.user} ({c.label()})')
        self.claims.append(new)
        self.next_id += 1
        return new

    def release(self, claim_id, user, is_maintainer=False):
        for c in self.claims:
            if c.id == claim_id:
                if c.user != user and not is_maintainer:
                    raise ClaimError(f'claim #{claim_id} belongs to {c.user}')
                self.claims.remove(c)
                return c
        raise ClaimError(f'no claim #{claim_id}')

    def touch(self, user, now):
        for c in self.claims:
            if c.user == user:
                c.last_activity = now

    def holder(self, addr, size=1, path=''):
        for c in self.claims:
            if c.covers(addr, size, path):
                return c
        return None

    def sweep(self, policy, now):
        """-> (expired, warn). Expired claims are removed."""
        expired = [c for c in self.claims if now - c.last_activity >= policy.claim_lapse_days * DAY]
        warn = [c for c in self.claims if c not in expired
                and now - c.last_activity >= policy.claim_warn_days * DAY]
        self.claims = [c for c in self.claims if c not in expired]
        return expired, warn

    # -- owners ------------------------------------------------------------
    def record_merge(self, author, addrs):
        for a in addrs:
            self.owners.setdefault(f'{a:08X}', author)

    def owner(self, addr):
        return self.owners.get(f'{addr:08X}')
