## DCC Bot

Modern Discord bot implementation for Dungeon Crawl Classics.

### Entry Point
Run the bot via `bot.py`:

```
python bot.py
```

### Legacy Code
Historical and experimental legacy files are retained under `old/` for reference (e.g., earlier monolithic `dccbot.py` versions). The root-level legacy `dccbot.py` has been removed to prevent syntax and import errors; all functionality has been modularized into cogs and helpers.

### Key Directories
 - `characters/` – Canonical folder for character JSON records (unified; no nested `characters/characters`).

### Familiar Support
Familiars are generated via the Find Familiar spell. Sheets and rename operations now support familiars with a compact display.

### Requirements
Install dependencies:
```
pip install -r requirements.txt
```

### Health Check
Run a lightweight diagnostic of environment and data integrity:
```
python scripts/health_check.py
```
Use `--strict` to get a non-zero exit code on failures (helpful for CI) and `--json` for machine-readable output.

### Environment
Provide a `token.env` or `.env` with `DISCORD_TOKEN=your_token_here` and optionally `GUILD_ID` for guild-specific sync.

### Backup
Nightly backups can be enabled with `NIGHTLY_BACKUP_ENABLED=1` and optional `NIGHTLY_BACKUP_UTC=HH:MM` (UTC) in the environment.

### Contributing
Please avoid reintroducing legacy monolithic scripts; add new features as cogs or modules. Submit PRs with focused changes and include tests or validation snippets when possible.

