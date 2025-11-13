from __future__ import annotations
import discord
from typing import Optional

PALETTE = {
    'info': 0x3498db,
    'success': 0x2ecc71,
    'error': 0xe74c3c,
    'warn': 0xf1c40f,
    'neutral': 0x95a5a6,
}

def _base(title: Optional[str]=None, description: Optional[str]=None, color: int | None=None) -> discord.Embed:
    emb = discord.Embed(title=title or discord.Embed.Empty, description=description or discord.Embed.Empty, color=color or PALETTE['neutral'])
    return emb

def info(desc: str, title: str='Info') -> discord.Embed:
    return _base(title, desc, PALETTE['info'])

def success(desc: str, title: str='Success') -> discord.Embed:
    return _base(title, desc, PALETTE['success'])

def error(desc: str, title: str='Error') -> discord.Embed:
    return _base(title, desc, PALETTE['error'])

def warn(desc: str, title: str='Warning') -> discord.Embed:
    return _base(title, desc, PALETTE['warn'])

def simple_field_embed(title: str, fields: list[tuple[str, str]], inline: bool=False, color: int | None=None) -> discord.Embed:
    emb = _base(title, None, color)
    for name, value in fields:
        emb.add_field(name=name, value=value, inline=inline)
    return emb
