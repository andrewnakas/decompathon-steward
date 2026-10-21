"""steward.toml: one file per project says which gates run, where, and what the merge policy is.

The policy is data, not prompt text: the decider reads it, the models never change it.
"""
import csv
import fnmatch
import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field

GATE_WHERE = ('public', 'judge')
GATE_KINDS = ('command', 'rexcheck-gate')
UNIT_KINDS = ('address', 'file')


def unit_label(u):
    """Units are retail addresses (int) or source files (str)."""
    return f'0x{u:08X}' if isinstance(u, int) else u


@dataclass
class Gate:
    name: str
    run: str
    where: str = 'public'       # public = GitHub-hosted, no secrets; judge = self-hosted sandbox with game files
    kind: str = 'command'       # how to read the output (steward/gates)
    required: bool = True
    timeout: int = 3600


@dataclass
class Policy:
    auto_merge: bool = True
    first_timer_needs_human: bool = True
    owner_rule_days: int = 7
    claim_max_bytes: int = 0x2000
    claim_max_open: int = 3
    claim_lapse_days: int = 14
    claim_warn_days: int = 10
    require_claim: bool = False
    max_units_per_pr: int = 20
    merge_method: str = 'merge'
    protected_paths: list = field(default_factory=list)
    derived_paths: list = field(default_factory=list)   # PRs may edit these; a required gate must regenerate + compare them
    maintainers: list = field(default_factory=list)
    require_progress: bool = True   # halo2 #424: keep a change only when it brings code closer to retail
    owner_rule_exempt_maintainers: bool = False
    required_checkboxes: list = field(default_factory=list)


@dataclass
class Config:
    name: str
    repo: str
    root: str
    default_branch: str = 'main'
    unit_kind: str = 'address'   # address: one function per file, named by address; file: a source file of many
    unit_regex: str = r'sub_([0-9A-Fa-f]{8})\.[a-z]+$'
    unit_paths: list = field(default_factory=lambda: ['**'])
    function_table: str = ''
    addr_column: str = 'addr'
    size_column: str = 'size'
    source_column: str = 'source'   # unit_kind=file: which column names the source file
    gates: list = field(default_factory=list)
    policy: Policy = field(default_factory=Policy)
    models: dict = field(default_factory=dict)
    github: dict = field(default_factory=dict)     # app = slug of Steward's GitHub App
    discord: dict = field(default_factory=dict)    # guild, announce_channel, maintainer_channel
    digest: str = ''            # sha256 of the toml, recorded in every audit entry

    def is_protected(self, path):
        if any(fnmatch.fnmatch(path, p) for p in self.policy.derived_paths):
            return False
        return any(fnmatch.fnmatch(path, p) for p in self.policy.protected_paths)

    def unit_of(self, path):
        """The unit a changed file holds (address int, or the path for unit_kind=file), or None."""
        if not any(fnmatch.fnmatch(path, p) for p in self.unit_paths):
            return None
        if self.unit_kind == 'file':
            return path
        m = re.search(self.unit_regex, path)
        return int(m.group(1), 16) if m else None

    def load_sizes(self):
        if not self.function_table:
            return {}
        with open(os.path.join(self.root, self.function_table), newline='') as f:
            rows = list(csv.DictReader(f))
        if self.unit_kind == 'file':
            sizes = {}
            for r in rows:
                if r.get(self.source_column):
                    sizes[r[self.source_column]] = sizes.get(r[self.source_column], 0) + int(r[self.size_column])
            return sizes
        return {int(r[self.addr_column], 16): int(r[self.size_column], 0) for r in rows}


def load(path):
    raw = open(path, 'rb').read()
    d = tomllib.loads(raw.decode('utf-8'))
    p = d.get('project', {})
    for key in ('name', 'repo'):
        if key not in p:
            raise ValueError(f'{path}: [project] needs {key}')
    gates = []
    for g in d.get('gates', []):
        gate = Gate(**g)
        if gate.where not in GATE_WHERE:
            raise ValueError(f'gate {gate.name}: where must be one of {GATE_WHERE}')
        if gate.kind not in GATE_KINDS:
            raise ValueError(f'gate {gate.name}: kind must be one of {GATE_KINDS}')
        gates.append(gate)
    if len({g.name for g in gates}) != len(gates):
        raise ValueError('gate names must be unique')
    if p.get('unit_kind', 'address') not in UNIT_KINDS:
        raise ValueError(f'unit_kind must be one of {UNIT_KINDS}')
    policy = Policy(**d.get('policy', {}))
    # Steward's own config and the gates' code are always protected, whatever the file says.
    for must in ('steward.toml', '.github/**'):
        if must not in policy.protected_paths:
            policy.protected_paths.append(must)
    here = os.path.dirname(os.path.abspath(path))
    root = os.path.normpath(os.path.join(here, p.pop('root', '.')))   # project checkout, relative to the toml
    return Config(root=root, gates=gates, policy=policy, models=d.get('models', {}),
                  github=d.get('github', {}), discord=d.get('discord', {}),
                  digest=hashlib.sha256(raw).hexdigest(), **p)
