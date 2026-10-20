from steward import commands, roadmap
from steward.state import State

NOW = 1_800_000_000.0


def test_parse_only_line_starts():
    assert commands.parse('please /claim 0x82000000\n/claim 0x82000040 0x80\n /unclaim #3') == [
        ('claim', ['0x82000040', '0x80']), ('unclaim', ['#3'])]


def test_claim_flow(cfg):
    st, sizes = State(), cfg.load_sizes()
    assert 'claim #1' in commands.run('claim', ['0x82000040'], 'alice', st, cfg, sizes, NOW)
    assert st.claims[0].end == 0x82000040 + 128
    assert commands.run('claim', ['82000080', '16'], 'bob', st, cfg, sizes, NOW).startswith('refused')
    assert 'not a function start' in commands.run('claim', ['0x82000044'], 'bob', st, cfg, sizes, NOW)
    assert commands.run('unclaim', ['#1'], 'bob', st, cfg, sizes, NOW).startswith('refused')
    assert 'released' in commands.run('unclaim', ['1'], 'alice', st, cfg, sizes, NOW)
    assert commands.run('unclaim', ['x'], 'alice', st, cfg, sizes, NOW).startswith('refused')


def test_consent_is_by_actor(cfg):
    st = State()
    commands.run('consent', ['#7'], 'bob', st, cfg, {}, NOW)
    assert '7:bob' in st.consents


def test_suggest_prefers_neighbours_of_done(cfg):
    rows = [{'addr': f'0x{a:08X}', 'size': '64', 'callees': '0', 'closure': 'pure'}
            for a in (0x82000000, 0x82000100, 0x82100000)]
    out = roadmap.suggest(rows, {0x82000000}, lambda a, s: False, n=5)
    assert [o[1] for o in out] == [0x82000100, 0x82100000] and out[0][4] is True


def test_suggest_skips_claimed_and_tiny(cfg):
    rows = [{'addr': '0x82000000', 'size': '4'}, {'addr': '0x82000010', 'size': '64'}]
    assert roadmap.suggest(rows, set(), lambda a, s: a == 0x82000010) == []


def test_stats():
    rows = [{'addr': '0x1', 'size': '30'}, {'addr': '0x2', 'size': '70'}]
    assert roadmap.stats(rows, {2})['percent'] == 70.0
