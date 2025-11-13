from __future__ import annotations
import abc
from typing import Protocol, Optional, List, Dict, Any
from models.character import Character
from . import files

# High-level abstraction: can later add a SQLite implementation.

class CharacterStorage(Protocol):
    async def list_character_names(self) -> List[str]: ...
    async def load_character(self, name: str) -> Optional[Character]: ...
    async def save_character(self, character: Character) -> None: ...

class JsonStorageEngine(CharacterStorage):
    """JSON-backed character storage using storage.files helpers."""
    async def list_character_names(self) -> List[str]:
        return await files.async_list_characters()

    async def load_character(self, name: str) -> Optional[Character]:
        return await files.async_load_character(name)

    async def save_character(self, character: Character) -> None:
        await files.async_save_character(character)

# Simple registry / factory
_default_engine: CharacterStorage | None = None

async def get_engine() -> CharacterStorage:
    global _default_engine
    if _default_engine is None:
        _default_engine = JsonStorageEngine()
    return _default_engine

__all__ = [
    "CharacterStorage",
    "JsonStorageEngine",
    "get_engine",
]
