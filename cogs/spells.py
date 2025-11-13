import os
import json
from typing import Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands


class SpellResultsView(discord.ui.View):
    """Button view to toggle spell results between summary and full text."""
    def __init__(self, cog: 'SpellsCog', data: dict, bucket: str, level: int, name: str, *, mode: str = 'summary', timeout: Optional[float] = 300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.data = data
        self.bucket = bucket
        self.level = level
        self.name = name
        self.mode = mode  # 'summary' | 'full'

    def _update_styles(self):
        try:
            self.summary_button.style = discord.ButtonStyle.primary if self.mode == 'summary' else discord.ButtonStyle.secondary
            self.full_button.style = discord.ButtonStyle.primary if self.mode == 'full' else discord.ButtonStyle.secondary
        except Exception:
            pass

    @discord.ui.button(label="Summary", style=discord.ButtonStyle.primary, custom_id="spell_results:summary")
    async def summary_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mode == 'summary':
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return
        self.mode = 'summary'
        emb = self.cog._build_spell_embed(self.data, self.bucket, self.level, self.name, results_mode='summary')
        self._update_styles()
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="Full text", style=discord.ButtonStyle.secondary, custom_id="spell_results:full")
    async def full_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mode == 'full':
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return
        self.mode = 'full'
        emb = self.cog._build_spell_embed(self.data, self.bucket, self.level, self.name, results_mode='full')
        self._update_styles()
        await interaction.response.edit_message(embed=emb, view=self)


class SpellsCog(commands.Cog):
    """Lookup spell descriptions by class, level, and name."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure the /spell command is synced and visible quickly (per-guild when possible)
        if self._synced:
            return
        try:
            if self.bot.guilds:
                for g in list(self.bot.guilds):
                    try:
                        await self.bot.tree.sync(guild=g)
                    except Exception:
                        continue
            else:
                try:
                    await self.bot.tree.sync()
                except Exception:
                    pass
        finally:
            self._synced = True

    # ---- helpers ----
    def _root_dir(self) -> str:
        return os.path.dirname(os.path.dirname(__file__))

    def _load_spells(self) -> dict:
        try:
            path = os.path.join(self._root_dir(), 'Spells.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _bucket_for_class(self, klass: str) -> Optional[str]:
        k = (klass or '').strip().lower()
        if k in {'wizard', 'mage', 'elf'}:
            return 'Wizard Spells'
        if k in {'cleric'}:
            return 'Cleric Spells'
        return None

    def _get_spell_blob(self, spells: dict, bucket: str, level: int, name: str) -> dict:
        return (
            spells.get('spells', {})
                  .get(bucket, {})
                  .get(f'level {int(level)}', {})
                  .get(name, {})
            or {}
        )

    # ---- embed builders ----
    def _build_spell_embed(self, data: dict, bucket: str, level: int, name: str, *, results_mode: str = 'summary') -> discord.Embed:
        """Builds the spell embed with results rendered per results_mode ('summary'|'full')."""
        blob = self._get_spell_blob(data, bucket, level, name)
        klass_label = 'Wizard' if bucket.startswith('Wizard') else 'Cleric'

        emb = discord.Embed(
            title=f"{name}",
            description=f"{klass_label} • Level {level}",
            color=discord.Color.blurple(),
        )
        def _get_text(v) -> str:
            if isinstance(v, dict):
                return str(v.get('text') or '')
            if isinstance(v, list):
                return "\n\n".join(str(x) for x in v)
            return str(v or '')

        rng = _get_text(blob.get('range'))
        dur = _get_text(blob.get('duration'))
        cast = _get_text(blob.get('casting_time'))
        save = _get_text(blob.get('save'))
        desc = _get_text(blob.get('description'))

        if rng:
            emb.add_field(name="Range", value=rng[:1024], inline=True)
        if dur:
            emb.add_field(name="Duration", value=dur[:1024], inline=True)
        if cast:
            emb.add_field(name="Casting Time", value=cast[:1024], inline=True)
        if save:
            emb.add_field(name="Save", value=save[:1024], inline=True)
        if desc:
            if len(desc) <= 2048:
                emb.description = (emb.description or "") + ("\n\n" if emb.description else "") + desc
            else:
                emb.add_field(name="Description", value=desc[:1024], inline=False)

        flags: List[str] = []
        if 'corruption' in blob:
            flags.append('Corruption')
        if 'misfire' in blob:
            flags.append('Misfire')
        if 'manifestation' in blob:
            flags.append('Manifestation')
        if flags:
            emb.add_field(name="Tables", value=", ".join(flags), inline=False)

        # Results
        self._add_results_fields(emb, blob, mode=results_mode)
        return emb

    def _add_results_fields(self, emb: discord.Embed, blob: dict, *, mode: str = 'summary') -> None:
        """Append results table fields to an embed. mode: 'summary' (truncated lines) or 'full'."""
        try:
            results = blob.get('results') if isinstance(blob.get('results'), dict) else None
            if not results:
                return
            import re as _re

            def _normalize_text(txt: str) -> str:
                return _re.sub(r"\s+", " ", str(txt or '').strip())

            def _line_text(v) -> str:
                t = v.get('text') if isinstance(v, dict) else v
                t = _normalize_text(str(t))
                if mode == 'summary':
                    n = 180
                    return t[:n] + ("…" if len(t) > n else "")
                return t

            lines: List[str] = []
            for k, v in results.items():
                lines.append(f"{str(k)}: {_line_text(v)}")

            # Chunk into multiple fields under 1024 chars
            chunk: list[str] = []
            size = 0
            part = 1
            for ln in lines:
                add = len(ln) + 1
                if size + add > 1000 and chunk:
                    emb.add_field(name=("Results" if part == 1 else f"Results (cont. {part})"), value="\n".join(chunk), inline=False)
                    chunk = []
                    size = 0
                    part += 1
                chunk.append(ln)
                size += add
            if chunk:
                emb.add_field(name=("Results" if part == 1 else f"Results (cont. {part})"), value="\n".join(chunk), inline=False)
        except Exception:
            # Don't fail the embed entirely if results rendering trips up
            pass

    def _find_spell_matches(
        self,
        spells: dict,
        name: str,
        bucket: Optional[str] = None,
        level: Optional[int] = None,
    ) -> List[Tuple[str, int, str]]:
        """Return list of (bucket, level, exact_name) matches for a spell name (case-insensitive).
        If bucket or level provided, restrict search accordingly.
        """
        out: List[Tuple[str, int, str]] = []
        name_l = str(name or '').strip().lower()
        if not name_l:
            return out
        buckets = [bucket] if bucket else ['Wizard Spells', 'Cleric Spells']
        for b in buckets:
            lvls = [level] if level in (1,2,3,4,5) else [1,2,3,4,5]
            for lv in lvls:
                pool = (
                    spells.get('spells', {})
                          .get(b, {})
                          .get(f'level {lv}', {})
                    or {}
                )
                for nm in pool.keys():
                    if str(nm).lower() == name_l:
                        out.append((b, lv, str(nm)))
        return out

    # ---- /spell ----
    @app_commands.command(name="spell", description="Show a spell description by class and level (class/level optional)")
    @app_commands.describe(
        klass="Class: wizard/mage/elf or cleric (optional)",
        level="Spell level (1-5, optional)",
        spell="Spell name (required)"
    )
    async def spell(self, interaction: discord.Interaction, spell: str, klass: Optional[str] = None, level: Optional[int] = None):
        # Resolve class/level if omitted by searching for an unambiguous match
        data = self._load_spells()
        chosen_bucket: Optional[str] = None
        chosen_level: Optional[int] = None
        chosen_name: Optional[str] = None

        # Normalize provided klass
        bucket_hint = self._bucket_for_class(klass or '') if klass else None

        # If both hints provided, try direct
        if bucket_hint and level in (1,2,3,4,5):
            blob = self._get_spell_blob(data, bucket_hint, int(level), spell)
            if not blob:
                # Case-insensitive exact name within that level/bucket
                matches = self._find_spell_matches(data, spell, bucket=bucket_hint, level=int(level))
                if matches:
                    chosen_bucket, chosen_level, chosen_name = matches[0]
                else:
                    await interaction.response.send_message("Spell not found.", ephemeral=True)
                    return
            else:
                chosen_bucket, chosen_level, chosen_name = bucket_hint, int(level), spell
        else:
            # Partial info or none: attempt disambiguation
            matches = self._find_spell_matches(data, spell, bucket=bucket_hint, level=level if level in (1,2,3,4,5) else None)
            if not matches:
                # If we had only level, try across both buckets at that level with looser name search? Keep exact to avoid surprises
                await interaction.response.send_message("Spell not found. Try specifying class and level.", ephemeral=True)
                return
            # Deduplicate duplicate tuples (just-in-case)
            seen = set()
            uniq = []
            for t in matches:
                if t in seen:
                    continue
                seen.add(t)
                uniq.append(t)
            if len(uniq) == 1:
                chosen_bucket, chosen_level, chosen_name = uniq[0]
            else:
                # Ambiguous: ask for more info, show up to 10 candidates
                bullets = []
                for b, lv, nm in uniq[:10]:
                    label = 'Wizard' if b.startswith('Wizard') else 'Cleric'
                    bullets.append(f"• {nm} — {label} L{lv}")
                extra = '' if len(uniq) <= 10 else f"\n… and {len(uniq)-10} more"
                await interaction.response.send_message(
                    "Multiple matches. Please specify class and/or level.\n" + "\n".join(bullets) + extra,
                    ephemeral=True,
                )
                return

        # At this point we have a resolved bucket/level/name
        assert chosen_bucket and chosen_level and chosen_name
        blob = self._get_spell_blob(data, chosen_bucket, chosen_level, chosen_name)
        klass_label = 'Wizard' if chosen_bucket.startswith('Wizard') else 'Cleric'

        # Build embed (summary by default)
        emb = self._build_spell_embed(data, chosen_bucket, chosen_level, chosen_name, results_mode='summary')

        try:
            # Attach toggle view if results exist
            view = None
            if isinstance(blob.get('results'), dict) and blob.get('results'):
                view = SpellResultsView(self, data, chosen_bucket, chosen_level, chosen_name, mode='summary')
                # Set initial styles
                view._update_styles()
            await interaction.response.send_message(embed=emb, view=view)
        except Exception:
            # Fallback to plain text if embed fails
            def _get_text(v) -> str:
                if isinstance(v, dict):
                    return str(v.get('text') or '')
                if isinstance(v, list):
                    return "\n\n".join(str(x) for x in v)
                return str(v or '')
            txt = [f"{chosen_name} — {klass_label} L{chosen_level}"]
            rng = _get_text(blob.get('range'))
            dur = _get_text(blob.get('duration'))
            cast = _get_text(blob.get('casting_time'))
            save = _get_text(blob.get('save'))
            desc = _get_text(blob.get('description'))
            if rng: txt.append(f"Range: {rng}")
            if dur: txt.append(f"Duration: {dur}")
            if cast: txt.append(f"Casting Time: {cast}")
            if save: txt.append(f"Save: {save}")
            if desc: txt.append("") ; txt.append(desc)
            await interaction.response.send_message("\n".join(txt))

    # ---- Autocomplete for spell name ----
    @spell.autocomplete('spell')
    async def ac_spell_name(self, interaction: discord.Interaction, current: str):
        cur = (current or '').strip().lower()
        # Read chosen klass and level from options
        klass = None
        level = None
        try:
            for opt in interaction.data.get('options', []):
                n = opt.get('name')
                if n == 'klass':
                    klass = opt.get('value')
                elif n == 'level':
                    try:
                        level = int(opt.get('value'))
                    except Exception:
                        pass
        except Exception:
            pass
        bucket = self._bucket_for_class(klass or '') if klass else None
        data = self._load_spells()
        choices: List[app_commands.Choice[str]] = []
        # Build candidate list depending on filters
        candidates: List[Tuple[str,int,str]] = []
        if bucket and level in (1,2,3,4,5):
            pool = data.get('spells', {}).get(bucket, {}).get(f'level {int(level)}', {}) or {}
            for nm in pool.keys():
                candidates.append((bucket, int(level), str(nm)))
        elif bucket and not level:
            for lv in (1,2,3,4,5):
                pool = data.get('spells', {}).get(bucket, {}).get(f'level {lv}', {}) or {}
                for nm in pool.keys():
                    candidates.append((bucket, lv, str(nm)))
        elif level in (1,2,3,4,5) and not bucket:
            for b in ['Wizard Spells', 'Cleric Spells']:
                pool = data.get('spells', {}).get(b, {}).get(f'level {int(level)}', {}) or {}
                for nm in pool.keys():
                    candidates.append((b, int(level), str(nm)))
        else:
            # No filters: list from both buckets all levels
            for b in ['Wizard Spells', 'Cleric Spells']:
                for lv in (1,2,3,4,5):
                    pool = data.get('spells', {}).get(b, {}).get(f'level {lv}', {}) or {}
                    for nm in pool.keys():
                        candidates.append((b, lv, str(nm)))
        # Deduplicate by name but show class/level in the label
        seen_names = set()
        for b, lv, nm in candidates:
            if cur and cur not in nm.lower():
                continue
            val = nm
            label_cls = 'Wizard' if b.startswith('Wizard') else 'Cleric'
            label = f"{nm} ({label_cls} L{lv})"
            # Keep all entries even if names repeat, to help disambiguation in UI
            choices.append(app_commands.Choice(name=label, value=val))
            if len(choices) >= 25:
                break
        return choices

    # ---- Autocomplete for klass ----
    @spell.autocomplete('klass')
    async def ac_spell_class(self, interaction: discord.Interaction, current: str):
        cur = (current or '').strip().lower()
        opts = ['wizard', 'mage', 'elf', 'cleric']
        out: List[app_commands.Choice[str]] = []
        for o in opts:
            if cur and cur not in o:
                continue
            out.append(app_commands.Choice(name=o.title(), value=o))
        return out

    # ---- Autocomplete for level ----
    @spell.autocomplete('level')
    async def ac_spell_level(self, interaction: discord.Interaction, current: str):
        cur = (current or '').strip()
        out: List[app_commands.Choice[int]] = []
        for lv in (1,2,3,4,5):
            s = str(lv)
            if cur and cur not in s:
                continue
            out.append(app_commands.Choice(name=f"Level {lv}", value=lv))
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(SpellsCog(bot))
