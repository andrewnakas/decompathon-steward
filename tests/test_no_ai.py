"""[ai] enabled = false: the deterministic core runs with no AI package, no model, no claude CLI."""
import subprocess
import sys
import textwrap

from steward import config

TOML = """
[project]
name = "toy"
repo = "o/toy"
[ai]
enabled = false
[models]
backend = "claude-code"
triage = "x"
"""


def test_ai_enabled_switch(tmp_path):
    p = tmp_path / "steward.toml"
    p.write_text(TOML)
    assert config.load(str(p)).ai_enabled is False
    p.write_text(TOML.replace("enabled = false", "enabled = true"))
    assert config.load(str(p)).ai_enabled is True
    p.write_text(TOML.split("[ai]")[0])            # no [models] at all: off too
    assert config.load(str(p)).ai_enabled is False


def test_core_imports_nothing_from_agents():
    # pydantic and the agents package are made unimportable; the core must not care
    code = textwrap.dedent("""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('pydantic', 'claude_agent_sdk', 'anthropic') or name.startswith('steward.agents'):
                    raise ImportError('blocked: ' + name)
        sys.meta_path.insert(0, Block())
        import steward.daemon, steward.actions, steward.decider, steward.report, steward.commands
        assert not [m for m in sys.modules if m.startswith('steward.agents')]
        print('ok')
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
