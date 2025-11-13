from __future__ import annotations
import os
from typing import Iterable, Optional
import discord

def _parse_id_list(value: Optional[str]) -> set[int]:
    if not value:
        return set()
    out: set[int] = set()
    for part in value.replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except Exception:
            continue
    return out

def check_roll_permission(interaction: discord.Interaction) -> tuple[bool, str | None]:
    """Enforce simple allow rules for /roll.

    Env vars:
      - ROLL_ALLOWED_ROLE_IDS: comma/semicolon-separated role IDs
      - ROLL_ALLOWED_CHANNEL_IDS: comma/semicolon-separated channel IDs
      - ROLL_BLOCK_DM: '1' to block in DMs

    Returns (allowed, reason). If not allowed, reason is a short message.
    """
    block_dm = os.getenv('ROLL_BLOCK_DM', '0') == '1'
    role_ids = _parse_id_list(os.getenv('ROLL_ALLOWED_ROLE_IDS'))
    chan_ids = _parse_id_list(os.getenv('ROLL_ALLOWED_CHANNEL_IDS'))

    # DM check
    if interaction.guild is None and block_dm:
        return False, "Rolling is disabled in DMs."

    # Channel allowlist
    if chan_ids and interaction.channel_id not in chan_ids:
        return False, "Rolling is not allowed in this channel."

    # Role allowlist (only in guild context)
    if role_ids and interaction.guild is not None:
        member = interaction.guild.get_member(interaction.user.id) if interaction.user else None
        # member may be None if not cached; allow if unknown rather than block
        if member and not any(r.id in role_ids for r in getattr(member, 'roles', [])):
            return False, "You lack a required role to use /roll."

    return True, None
