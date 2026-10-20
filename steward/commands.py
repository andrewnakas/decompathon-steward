"""Slash commands from GitHub comments or Discord, parsed without a model.

The actor is the authenticated GitHub/Discord user from the event, never text in the message.
  /claim 0xADDR SIZE      /claim 0xADDR (one function, size from the table)     /claim path/to/file
  /unclaim ID             /consent PR   (owner lets PR change their merged work)
  /next                   (suggest functions)            /status
"""
import re
import time

from .state import ClaimError

CMD = re.compile(r'^\s*/(claim|unclaim|consent|next|status)\b[ \t]*(.*)$', re.M)
ADDR = re.compile(r'^(?:0x)?([0-9A-Fa-f]{8})$')


def parse(text):
    """-> list of (command, args list); only lines that start with a command count."""
    return [(m.group(1), m.group(2).split()) for m in CMD.finditer(text or '')]


def run(cmd, args, actor, state, cfg, sizes, now=None, is_maintainer=False):
    """Apply one command to state. -> reply text. Raises nothing; errors are replies."""
    now = now or time.time()
    pol = cfg.policy
    try:
        if cmd == 'claim':
            if not args:
                return 'usage: /claim 0xADDR [SIZE] or /claim path/to/file'
            m = ADDR.match(args[0])
            if m:
                start = int(m.group(1), 16)
                size = int(args[1], 0) if len(args) > 1 else sizes.get(start, 0)
                if not size:
                    return f'0x{start:08X} is not a function start; give a size'
                c = state.claim(actor, pol, now, start=start, size=size)
            else:
                c = state.claim(actor, pol, now, path=args[0])
            return f'claim #{c.id}: @{actor} has {c.label()} (lapses after {pol.claim_lapse_days} days without activity)'
        if cmd == 'unclaim':
            c = state.release(int(args[0].lstrip('#')), actor, is_maintainer)
            return f'released claim #{c.id} ({c.label()})'
        if cmd == 'consent':
            pr = int(args[0].lstrip('#'))
            key = f'{pr}:{actor}'
            state.consents[key] = now
            return f'@{actor} consents to PR #{pr} changing their merged functions; credit stays with them'
        if cmd in ('next', 'status'):
            return None     # answered by roadmap/progress, not state
    except (ClaimError, ValueError, IndexError) as e:
        return f'refused: {e}'
    return None
