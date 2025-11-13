from __future__ import annotations
from typing import Dict, Any

# Migration handlers map old_version -> function(data_dict) -> new_data_dict
MIGRATIONS = {}
LATEST_SCHEMA_VERSION = 1

def migrate_character_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    version = int(data.get("schema_version") or data.get("schemaVersion") or 1)
    # Future: while version < LATEST_SCHEMA_VERSION: apply MIGRATIONS[version](data); version += 1
    if "schema_version" not in data:
        data["schema_version"] = version
    return data

__all__ = ["migrate_character_dict", "LATEST_SCHEMA_VERSION"]
