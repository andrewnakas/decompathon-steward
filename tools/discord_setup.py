#!/usr/bin/env python3
"""Lay out a Decompathon community server the way decomp/modding Discords usually look, idempotently.

  python tools/discord_setup.py --guild <id> [--project thps64:"Tony Hawk's Pro Skater (N64)"] [--dry-run]
  python tools/discord_setup.py --invite      # prints the bot invite link (needs only the token)

Token: ~/.decompathon/discord-token.txt (never printed). The bot needs Administrator while this runs; the server
owner can lower it afterwards. Re-running only adds what is missing and refreshes the bot's own pinned posts, so it
is safe after hand edits (channels/roles it does not know are left alone).

Result: roles, categories, channels with read-only/staff overwrites, Community mode (rules + updates channels,
forums, announcement channel), onboarding questions that hand out ping/interest roles, the bot's welcome/rules/FAQ
posts, and ~/.decompathon/discord-<guild>.json with the ids Steward's [discord] section needs.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = 'https://discord.com/api/v10'
HOME = os.path.join(os.path.expanduser('~'), '.decompathon')

# permission bits
VIEW, SEND, SEND_THREADS, ADD_REACTIONS, CONNECT, SPEAK = 1 << 10, 1 << 11, 1 << 38, 1 << 6, 1 << 20, 1 << 21
MANAGE_MESSAGES, MANAGE_THREADS, KICK, BAN, TIMEOUT, ADMIN = 1 << 13, 1 << 34, 1 << 1, 1 << 2, 1 << 40, 1 << 3
MANAGE_CHANNELS, MANAGE_ROLES, MANAGE_NICKS, VIEW_AUDIT, MENTION_EVERYONE = 1 << 4, 1 << 28, 1 << 27, 1 << 7, 1 << 17
TEXT, VOICE, CATEGORY, ANNOUNCEMENT, FORUM = 0, 2, 4, 5, 15

MOD_PERMS = MANAGE_MESSAGES | MANAGE_THREADS | KICK | BAN | TIMEOUT | MANAGE_NICKS | VIEW_AUDIT

# name, colour, hoist, mentionable, permissions (None = leave default)
ROLES = [
    ('Maintainer', 0xE67E22, True, True, ADMIN),
    ('Moderator', 0x3498DB, True, True, MOD_PERMS | MENTION_EVERYONE),
    ('Contributor', 0x2ECC71, True, False, None),       # Steward grants it after a merged PR
    ('Decomper', 0x9B59B6, False, False, None),
    ('Tool dev', 0x1ABC9C, False, False, None),
    ('Porter', 0xF1C40F, False, False, None),
    ('Playtester', 0x95A5A6, False, False, None),
    ('Announcements ping', 0x7F8C8D, False, True, None),
    ('Jam ping', 0x7F8C8D, False, True, None),
]

READ_ONLY = 'read_only'      # everyone reads, staff + bot post
STAFF = 'staff'              # Maintainer/Moderator only

# category -> [(name, type, topic, mode, extra)]
LAYOUT = [
    ('START HERE', [
        ('welcome', TEXT, 'What Decompathon is and where to go next.', READ_ONLY, {}),
        ('rules', TEXT, 'Read before posting. Breaking these gets you removed.', READ_ONLY, {}),
        ('announcements', ANNOUNCEMENT, 'Jams, releases and project news. Follow it from your own server.', READ_ONLY, {}),
        ('faq', TEXT, 'What a decomp is, how matching works, how to start.', READ_ONLY, {}),
    ]),
    ('COMMUNITY', [
        ('general', TEXT, 'Talk about decomps, recomps, ports and the games themselves.', None, {}),
        ('introductions', TEXT, 'Say hi: what you play, what you can do, what you want to learn.', None, {}),
        ('showcase', TEXT, 'Screenshots, clips and builds of your work. Discuss in threads.', None, {'rate_limit_per_user': 30}),
        ('off-topic', TEXT, 'Anything else (keep it civil).', None, {}),
    ]),
    ('DECOMP', [
        ('decomp-help', FORUM, 'One post per question. Include the function, your C, the diff and what you tried.', None,
         {'tags': ['matching', 'tooling', 'build', 'recomp', 'asm', 'solved']}),
        ('matching', TEXT, 'Live chat while you match: diffs, permuter results, compiler quirks.', None, {}),
        ('tools-and-setup', TEXT, 'Toolchains, splat, m2c, objdiff, permuter, recompilers, WSL/Windows setup.', None, {}),
        ('claims', TEXT, 'Steward posts claim changes here. Claim with /claim, see yours with /status.', READ_ONLY, {}),
        ('progress', TEXT, 'Merged PRs, new matches and milestones (Steward feed).', READ_ONLY, {}),
    ]),
    ('PROJECTS', [
        ('project-proposals', FORUM, 'Pitch a game for a jam: platform, compiler, existing work, why it is a fit.', None,
         {'tags': ['N64', 'PS1', 'PS2', 'GameCube', 'Xbox', '360', 'PC', 'other']}),
    ]),
    ('VOICE', [
        ('General', VOICE, None, None, {}),
        ('Pairing', VOICE, None, None, {}),
        ('Jam stage', VOICE, None, None, {}),
    ]),
    ('STAFF', [
        ('maintainers', TEXT, 'Escalations from Steward, policy and moderation talk.', STAFF, {}),
        ('steward-log', TEXT, 'Every Steward decision (audit feed).', STAFF, {}),
        ('mod-log', TEXT, 'Discord moderation actions.', STAFF, {}),
    ]),
]

WELCOME = """# Welcome to Decompathon
Decompathon runs **decomp jams**: a game picked as the target, a public repo, and a community racing to turn its machine code back into matching C, one function at a time. An open-source maintainer bot, **Steward**, checks every pull request against the original binary, keeps the claims board and posts progress. Humans own the rules.

**Get started**
1. Read <#{rules}>.
2. Pick your interests in **Channels & Roles** (top of the channel list).
3. New to decomp? Start with <#{faq}>, then ask in <#{help}>.
4. Link your GitHub with `/link`, then `/next` suggests a function to try.

**Current pilot:** {projects}
Say hi in <#{intro}>."""

RULES = """# Rules
1. **Be decent.** No harassment, slurs or personal attacks. Critique code, not people.
2. **No ROMs, ISOs, BIOS files or game assets.** Don't post them, link them or ask for them. Projects require your own legally obtained copy.
3. **No leaked or stolen source code.** Code from leaks or from any project that hasn't opted in is not allowed, and neither are names copied from it.
4. **Credit people.** Keep authorship on what you build on, and follow each project's licence.
5. **Say when AI helped.** PRs record "none / assisted / generated". Steward's own messages are always labelled as automated.
6. **Use the right channel.** Questions go in <#{help}> as forum posts, one per question.
7. **Claims are respected.** Check `/status` before working on a function; don't snipe someone else's claim.
8. **Moderators' calls stand.** Appeal in a DM to a Maintainer, not in public.

Breaking 1-3 means removal. Everything else gets a warning first."""

FAQ = """# FAQ
**What is a decomp?** C source that compiles back to the *exact* bytes of the original game. Because it matches byte for byte, the code is known to behave like the original, and it can then be ported, modded and preserved.

**What is "matching"?** Write C for one function, compile it with the game's original compiler and flags, and compare against the original assembly. Same bytes = matched.

**What do I need?** Your own copy of the game, git, Python, and the project's toolchain (each repo's README has one-line setup). Windows, Linux and macOS all work.

**How do I start?** `/link` your GitHub, then `/next` for a starter function, then `/claim 0xADDR`. Open a PR when it matches. Steward runs the gates and merges green PRs; the first one for a new contributor gets a human review.

**I'm stuck on a function.** Post in <#{help}> with the function name, your C, the diff and what you tried. decomp-permuter and m2c drafts are fine starting points.

**Can I use AI?** Yes, but say so in the PR template, and you must understand what you submit. Names and comments still need to be justified, not guessed."""


def token():
    return open(os.path.join(HOME, 'discord-token.txt'), encoding='utf-8-sig').read().strip()


class Api:
    def __init__(self, dry):
        self.dry, self.t = dry, token()

    def call(self, method, path, body=None):
        if self.dry and method != 'GET':
            print(f'  (dry) {method} {path}')
            return {'id': '0'}
        data = None if body is None else json.dumps(body).encode()
        for _ in range(8):
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                'Authorization': f'Bot {self.t}', 'Content-Type': 'application/json',
                'User-Agent': 'DecompathonSteward (https://github.com/andrewnakas, 1.0)'})
            try:
                with urllib.request.urlopen(req) as r:
                    txt = r.read()
                    return json.loads(txt) if txt else {}
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors='replace')
                if e.code == 429:
                    time.sleep(float(json.loads(txt).get('retry_after', 1)) + 0.2)
                    continue
                sys.exit(f'{method} {path} -> {e.code}: {txt[:400]}')
        sys.exit(f'{method} {path}: rate limited too long')


def overwrites(mode, guild, roles, bot_id):
    if mode is None:
        return []
    staff = [roles['Maintainer'], roles['Moderator']]
    out = [{'id': bot_id, 'type': 1, 'allow': str(VIEW | SEND | SEND_THREADS | MANAGE_MESSAGES), 'deny': '0'}]
    if mode == READ_ONLY:
        out.append({'id': guild, 'type': 0, 'allow': str(VIEW), 'deny': str(SEND | SEND_THREADS | ADD_REACTIONS)})
        out += [{'id': r, 'type': 0, 'allow': str(SEND | SEND_THREADS), 'deny': '0'} for r in staff]
    elif mode == STAFF:
        out.append({'id': guild, 'type': 0, 'allow': '0', 'deny': str(VIEW)})
        out += [{'id': r, 'type': 0, 'allow': str(VIEW | SEND), 'deny': '0'} for r in staff]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--guild')
    ap.add_argument('--project', action='append', default=[], help='slug:"Title" (one channel + ping role each)')
    ap.add_argument('--invite', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    api = Api(a.dry_run)
    me = api.call('GET', '/users/@me')
    if a.invite:
        app = api.call('GET', '/oauth2/applications/@me')
        print(f'https://discord.com/oauth2/authorize?client_id={app["id"]}&scope=bot+applications.commands&permissions={ADMIN}')
        return
    if not a.guild:
        sys.exit('--guild <id> needed (Developer Mode on -> right-click the server -> Copy Server ID)')
    g = a.guild
    projects = [p.split(':', 1) for p in a.project] or [['thps64', "Tony Hawk's Pro Skater (N64)"]]
    print(f'bot {me["username"]} -> guild {api.call("GET", f"/guilds/{g}")["name"]}')

    # roles
    have = {r['name']: r['id'] for r in api.call('GET', f'/guilds/{g}/roles')}
    roles = {}
    for name, colour, hoist, ment, perms in ROLES + [(f'{t} ping', 0x7F8C8D, False, True, None) for s, t in projects]:
        if name not in have:
            body = {'name': name, 'color': colour, 'hoist': hoist, 'mentionable': ment}
            if perms is not None:
                body['permissions'] = str(perms)
            have[name] = api.call('POST', f'/guilds/{g}/roles', body)['id']
            print(f'  role {name}')
        roles[name] = have[name]

    # categories + channels
    chans = api.call('GET', f'/guilds/{g}/channels')
    by = {(c.get('parent_id'), c['name'].lower()): c for c in chans}
    cats = {c['name'].upper(): c for c in chans if c['type'] == CATEGORY}
    ids = {}
    layout = [(cat, [(s, TEXT, f'{t}: progress, questions and claims talk for this project.', None, {})
                     for s, t in projects] + items if cat == 'PROJECTS' else items) for cat, items in LAYOUT]
    for pos, (cat, items) in enumerate(layout):
        if cat not in cats:
            cats[cat] = api.call('POST', f'/guilds/{g}/channels', {'name': cat, 'type': CATEGORY, 'position': pos,
                                  'permission_overwrites': overwrites(STAFF, g, roles, me['id']) if cat == 'STAFF' else []})
            print(f'  category {cat}')
        pid = cats[cat]['id']
        for name, typ, topic, mode, extra in items:
            key = (pid, name.lower())
            if key in by:
                ids[name] = by[key]['id']
                continue
            # the default #general (or any same-named channel outside our categories) is moved, not duplicated
            ours = {c['id'] for c in cats.values()}
            stray = [c for c in chans if c['name'].lower() == name.lower() and c['type'] == typ and c.get('parent_id') not in ours]
            if stray:
                body = {'parent_id': pid}
                if mode is not None or cat == 'STAFF':
                    body['permission_overwrites'] = overwrites(mode if cat != 'STAFF' else STAFF, g, roles, me['id'])
                if topic and typ != VOICE:
                    body['topic'] = topic
                api.call('PATCH', f'/channels/{stray[0]["id"]}', body)
                ids[name] = stray[0]['id']
                by[key] = stray[0]
                print(f'  #{name} (moved)')
                continue
            body = {'name': name, 'type': typ, 'parent_id': pid,
                    'permission_overwrites': overwrites(mode if cat != 'STAFF' else STAFF, g, roles, me['id'])}
            if topic and typ != VOICE:
                body['topic'] = topic
            if 'rate_limit_per_user' in extra:
                body['rate_limit_per_user'] = extra['rate_limit_per_user']
            if typ == FORUM:
                body['available_tags'] = [{'name': t} for t in extra.get('tags', [])]
                body['default_reaction_emoji'] = {'emoji_name': '\N{WHITE HEAVY CHECK MARK}'}
            ids[name] = api.call('POST', f'/guilds/{g}/channels', body)['id']
            print(f'  #{name}')

    # Community mode: rules + updates channels, then onboarding
    guild = api.call('GET', f'/guilds/{g}')
    if 'COMMUNITY' not in guild.get('features', []):
        api.call('PATCH', f'/guilds/{g}', {'features': guild.get('features', []) + ['COMMUNITY'],
                                          'verification_level': 2, 'explicit_content_filter': 2,
                                          'rules_channel_id': ids['rules'], 'public_updates_channel_id': ids['maintainers'],
                                          'description': 'Decomp jams: turn game binaries back into matching C, together.'})
        print('  community mode on')
    api.call('PATCH', f'/guilds/{g}', {'system_channel_id': ids['introductions'], 'system_channel_flags': 0})

    proj_opts = [{'title': t, 'description': f'#{s} + its ping role', 'role_ids': [roles[f'{t} ping']],
                  'channel_ids': [ids[s]]} for s, t in projects]
    onboarding = {'enabled': True, 'mode': 0, 'default_channel_ids': [ids[c] for c in
                  ('welcome', 'rules', 'announcements', 'faq', 'general', 'introductions', 'showcase', 'decomp-help', 'progress')],
                  'prompts': [
                      {'type': 0, 'title': 'What brings you here?', 'single_select': False, 'required': False, 'in_onboarding': True,
                       'options': [
                           {'title': 'I want to decompile', 'description': 'Matching functions, claims, help forum',
                            'role_ids': [roles['Decomper']], 'channel_ids': [ids['matching'], ids['claims']]},
                           {'title': 'I build tools', 'description': 'Toolchains, splat, recompilers',
                            'role_ids': [roles['Tool dev']], 'channel_ids': [ids['tools-and-setup']]},
                           {'title': 'I port and mod', 'description': 'Recomps, ports, mods built on decomps',
                            'role_ids': [roles['Porter']], 'channel_ids': []},
                           {'title': 'I play and test', 'description': 'Try builds and report bugs',
                            'role_ids': [roles['Playtester']], 'channel_ids': []},
                       ]},
                      {'type': 0, 'title': 'Which projects?', 'single_select': False, 'required': False, 'in_onboarding': True,
                       'options': proj_opts},
                      {'type': 0, 'title': 'Pings', 'single_select': False, 'required': False, 'in_onboarding': True,
                       'options': [
                           {'title': 'Announcements', 'description': 'Releases and big news',
                            'role_ids': [roles['Announcements ping']], 'channel_ids': []},
                           {'title': 'Jams', 'description': 'When a jam starts or ends',
                            'role_ids': [roles['Jam ping']], 'channel_ids': []},
                       ]},
                  ]}
    api.call('PUT', f'/guilds/{g}/onboarding', onboarding)
    print('  onboarding set')

    # the bot's own posts: edit in place when they exist
    fmt = {'rules': ids['rules'], 'faq': ids['faq'], 'help': ids['decomp-help'], 'intro': ids['introductions'],
           'projects': ', '.join(f'**{t}** in <#{ids[s]}>' for s, t in projects)}
    for chan, text in (('welcome', WELCOME), ('rules', RULES), ('faq', FAQ)):
        body = {'content': text.format(**fmt), 'allowed_mentions': {'parse': []}}
        mine = [m for m in api.call('GET', f'/channels/{ids[chan]}/messages?limit=20') if m['author']['id'] == me['id']]
        if mine:
            api.call('PATCH', f'/channels/{ids[chan]}/messages/{mine[-1]["id"]}', body)
        else:
            api.call('POST', f'/channels/{ids[chan]}/messages', body)
    print('  welcome/rules/faq posted')

    out = {'guild': g, 'announce_channel': ids['progress'], 'maintainer_channel': ids['maintainers'],
           'claims_channel': ids['claims'], 'log_channel': ids['steward-log'], 'announcements': ids['announcements'],
           'contributor_role': roles['Contributor']}
    p = os.path.join(HOME, f'discord-{g}.json')
    if not a.dry_run:
        json.dump(out, open(p, 'w'), indent=1)
    print(f'ids -> {p}\n[discord] for steward.toml:\n' + '\n'.join(f'{k} = "{v}"' for k, v in out.items()))


if __name__ == '__main__':
    main()
