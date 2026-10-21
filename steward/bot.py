"""Steward on Discord + GitHub in one process (the judge box / owner's PC).

  python -m steward.bot --repo owner/name --app <slug> [--interval 60] [--no-ai]

Bot token: ~/.decompathon/discord-token.txt (or DISCORD_TOKEN). Channels come from the repo's
steward.toml [discord] section: guild, announce_channel, maintainer_channel (ids).
GitHub stays the source of truth: Discord commands act as the GitHub account a user linked
with /link (proved by commenting the code on GitHub), so nobody can claim as someone else.

Slash commands: /link /claim /unclaim /next /status /ask
"""
import argparse
import asyncio
import os
import traceback

import discord
from discord import app_commands

from .agents import roles
from .daemon import Steward
from .github import App, Repo

FOOT = '-# Steward (automated)'


def token():
    t = os.environ.get('DISCORD_TOKEN')
    if t:
        return t.strip()
    p = os.path.join(os.path.expanduser('~'), '.decompathon', 'discord-token.txt')
    return open(p, encoding='utf-8-sig').read().strip()


def make_bot(steward, interval):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def login_for(user):
        return steward.memo['links'].get(str(user.id))

    async def run(fn, *a):
        return await asyncio.to_thread(fn, *a)

    @tree.command(description='Link your Discord account to your GitHub account')
    async def link(inter: discord.Interaction):
        code = await run(steward.link_code, inter.user.id)
        issue = steward.memo.get('claims_issue')
        where = f'https://github.com/{steward.repo.repo}/issues/{issue}' if issue else f'any issue in {steward.repo.repo}'
        await inter.response.send_message(f'Comment `/link {code}` on {where} from your GitHub account.', ephemeral=True)

    @tree.command(description='Claim a function (0xADDR) or a file')
    @app_commands.describe(target='0xADDR or path/to/file', size='bytes (optional for a function start)')
    async def claim(inter: discord.Interaction, target: str, size: str = ''):
        login = login_for(inter.user)
        if not login:
            await inter.response.send_message('Link your GitHub account first: /link', ephemeral=True)
            return
        r = await run(steward.run_command, 'claim', [target] + ([size] if size else []), login)
        await inter.response.send_message(f'{r}\n{FOOT}')

    @tree.command(description='Release one of your claims')
    async def unclaim(inter: discord.Interaction, claim_id: int):
        login = login_for(inter.user)
        if not login:
            await inter.response.send_message('Link your GitHub account first: /link', ephemeral=True)
            return
        r = await run(steward.run_command, 'unclaim', [str(claim_id)], login)
        await inter.response.send_message(f'{r}\n{FOOT}')

    @tree.command(description='Suggest unclaimed functions to work on')
    async def next(inter: discord.Interaction):
        await inter.response.defer()
        await inter.followup.send(f'{await run(steward.run_command, "next", [], "")}\n{FOOT}')

    @tree.command(description='Project progress')
    async def status(inter: discord.Interaction):
        await inter.response.defer()
        await inter.followup.send(f'{await run(steward.run_command, "status", [], "")}\n{FOOT}')

    @tree.command(description='Ask a question about contributing (answered from the project docs)')
    async def ask(inter: discord.Interaction, question: str):
        await inter.response.defer(thinking=True)

        def answer():
            docs = {}
            for path in ('README.md', 'RULES.md', 'CONTRIBUTING.md'):
                try:
                    docs[path] = steward.repo.file_text(path, steward.branch)
                except Exception:
                    pass
            out, meta = roles.make('mentor', steward.cfg, docs=docs).run(question, str(inter.user))
            if out is None:
                return 'I could not answer that one; a maintainer will pick it up.'
            src = f' (from {", ".join(out.sources)})' if out.sources else ''
            return out.answer[:1800] + src + ('\n_A maintainer should confirm this._' if out.needs_human else '')
        await inter.followup.send(f'{await run(answer)}\n-# Steward (AI, answers from the project docs)')

    async def post(channel_id, text):
        if not channel_id:
            return
        ch = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
        await ch.send(text[:1900])

    async def loop():
        await client.wait_until_ready()
        while not client.is_closed():
            try:
                events = await run(steward.tick)
                dc = steward.cfg.discord
                for e in events:
                    if e['kind'] == 'merged':
                        await post(dc.get('announce_channel'), f'Merged: {e["text"]}\n<{e["url"]}>')
                    elif e['kind'] == 'escalated':
                        await post(dc.get('maintainer_channel'), f'Needs a maintainer: #{e["pr"]} {e["text"]}\n<{e["url"]}>')
                    elif e['kind'] in ('claim', 'unclaim', 'claim_expired', 'claim_warn'):
                        await post(dc.get('announce_channel'), e['text'])
                    elif e['kind'] == 'linked':
                        user = await client.fetch_user(int(e['discord_id']))
                        await user.send(f'Linked to GitHub account @{e["login"]}.')
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(interval)

    @client.event
    async def on_ready():
        guild = steward.cfg.discord.get('guild')
        if guild:
            g = discord.Object(id=int(guild))
            tree.copy_global_to(guild=g)
            await tree.sync(guild=g)
        else:
            await tree.sync()
        print(f'discord: logged in as {client.user}', flush=True)

    @client.event
    async def setup_hook():
        client.loop.create_task(loop())

    client.setup_hook = setup_hook
    return client


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--app', required=True)
    ap.add_argument('--data')
    ap.add_argument('--interval', type=int, default=60)
    ap.add_argument('--no-ai', action='store_true')
    a = ap.parse_args()
    data = a.data or os.path.join(os.path.expanduser('~'), '.decompathon', 'data', a.repo.replace('/', '_'))
    steward = Steward(Repo(App(a.app), a.repo), data, use_ai=not a.no_ai)
    steward.refresh_config()
    make_bot(steward, a.interval).run(token(), log_handler=None)


if __name__ == '__main__':
    main()
