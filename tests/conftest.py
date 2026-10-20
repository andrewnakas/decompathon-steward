import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steward import config  # noqa: E402

TOML = '''
[project]
name = "toy"
repo = "toy/toy"
unit_paths = ["decomp/**"]
unit_regex = 'sub_([0-9A-Fa-f]{8})\\.cpp$'
function_table = "functions.csv"

[policy]
protected_paths = ["tools/**", "functions.csv"]
required_checkboxes = ["No game files", "Provenance is honest"]

[[gates]]
name = "scan"
run = "python -c \\"print('ok')\\""

[[gates]]
name = "oracle"
run = "echo"
where = "judge"
kind = "rexcheck-gate"
'''

CSV = 'addr,size\n0x82000000,64\n0x82000040,128\n0x82000100,4096\n0x82002000,32\n'

BODY = '- [x] No game files, recompiled code or retail bytes\n- [x] Provenance is honest\n'


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / 'steward.toml').write_text(TOML)
    (tmp_path / 'functions.csv').write_text(CSV)
    return config.load(str(tmp_path / 'steward.toml'))
