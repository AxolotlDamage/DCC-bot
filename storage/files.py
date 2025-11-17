import os, json, asyncio, tempfile
from typing import Any, Dict, List, Optional
from models.character import Character

try:
    from core.config import SAVE_FOLDER  # optional central save folder
    # Use SAVE_FOLDER directly (already points to the characters directory). Avoid nesting.
    BASE_DIR = SAVE_FOLDER
    # If previous versions created a nested characters/characters directory, optionally migrate.
    legacy_nested = os.path.join(SAVE_FOLDER, "characters")
    if os.path.isdir(legacy_nested) and not os.path.samefile(legacy_nested, SAVE_FOLDER):
        # Move any json files up one level if they are missing in BASE_DIR.
        for fname in os.listdir(legacy_nested):
            if fname.lower().endswith('.json'):
                src = os.path.join(legacy_nested, fname)
                dst = os.path.join(BASE_DIR, fname)
                if not os.path.exists(dst):
                    try:
                        os.replace(src, dst)
                    except OSError:
                        pass
        # Leave legacy_nested (may contain non-json artifacts) but do not use it further.
except Exception:
    BASE_DIR = "characters"

__all__ = [
    "async_list_characters",
    "async_load_character",
    "async_save_character",
    # Compatibility / generic JSON helpers
    "async_list_json_files",
    "async_load_json",
    "async_save_json",
]

SCHEMA_VERSION = 1  # increment when structure changes

async def _run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("_", "-", " ")).strip()

def _char_path(name: str) -> str:
    return os.path.join(BASE_DIR, f"{_safe_name(name)}.json")

async def async_list_characters() -> List[str]:
    def _list():
        if not os.path.isdir(BASE_DIR):
            return []
        return [f[:-5] for f in os.listdir(BASE_DIR) if f.lower().endswith(".json")]
    return await _run_blocking(_list)

# Generic JSON wrappers (useful for other data types later)
async def async_list_json_files() -> List[str]:
    return await async_list_characters()

async def async_load_json(name: str) -> Optional[Dict[str, Any]]:
    path = _char_path(name)
    if not os.path.exists(path):
        return None
    def _load():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        return await _run_blocking(_load)
    except json.JSONDecodeError:
        return None

async def async_save_json(name: str, data: Dict[str, Any]) -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    path = _char_path(name)
    def _save():
        fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    await _run_blocking(_save)

async def async_load_character(name: str) -> Optional[Character]:
    raw = await async_load_json(name)
    if raw is None:
        return None
    # Future: handle migrations based on raw.get("schema_version")
    return Character.from_dict(raw)

async def async_save_character(char: Character) -> None:
    data = char.to_dict()
    if "schema_version" not in data:
        data["schema_version"] = SCHEMA_VERSION
    await async_save_json(char.name, data)
