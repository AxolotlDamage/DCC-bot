from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

AbilityBlock = Dict[str, Any]

@dataclass
class Character:
    name: str
    owner: Optional[int] = None
    level: int = 0
    char_class: str = ""
    alignment: Optional[str] = None
    abilities: Dict[str, AbilityBlock] = field(default_factory=dict)
    luck: Dict[str, int] = field(default_factory=lambda: {"current": 0, "max": 0})
    weapon: Any = None  # could be str or dict
    weapons: List[Dict[str, Any]] = field(default_factory=list)
    attacks: List[Dict[str, Any]] = field(default_factory=list)
    hp: Any = None  # may be dict or int
    ac: Optional[int] = None
    notes: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Character":
        return Character(
            name=data.get("name", "Unnamed"),
            owner=data.get("owner"),
            level=int(data.get("level", 0)),
            char_class=data.get("class", ""),
            alignment=data.get("alignment"),
            abilities=data.get("abilities", {}),
            luck=data.get("luck", {"current": 0, "max": 0}),
            weapon=data.get("weapon"),
            weapons=data.get("weapons", []),
            attacks=data.get("attacks", []),
            hp=data.get("hp"),
            ac=data.get("ac"),
            notes=data.get("notes", {}),
            schema_version=int(data.get("schema_version", data.get("schemaVersion", 1) or 1)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "level": self.level,
            "class": self.char_class,
            "alignment": self.alignment,
            "abilities": self.abilities,
            "luck": self.luck,
            "weapon": self.weapon,
            "weapons": self.weapons,
            "attacks": self.attacks,
            "hp": self.hp,
            "ac": self.ac,
            "notes": self.notes,
            "schema_version": self.schema_version,
        }

    def current_hp(self) -> int:
        hp_field = self.hp
        if isinstance(hp_field, dict):
            return int(hp_field.get("current", hp_field.get("max", 0)) or 0)
        if isinstance(hp_field, int):
            return hp_field
        return 0

    def set_current_hp(self, value: int) -> None:
        if not isinstance(self.hp, dict):
            self.hp = {"current": int(value), "max": int(max(value, self.current_hp()))}
        else:
            self.hp["current"] = int(value)

__all__ = ["Character"]
