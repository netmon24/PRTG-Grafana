"""Domänenobjekte, unabhängig von der Form der PRTG-Antworten."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(Enum):
    """Statuswert mit Influx-Code und Schwere (höher = schlechter, pausiert wird nicht gewertet)."""

    UP = ("up", 0, 0)
    WARNING = ("warning", 1, 2)
    DOWN = ("down", 2, 3)
    PAUSED = ("paused", 3, -1)
    UNKNOWN = ("unknown", 4, 1)

    def __init__(self, label: str, code: int, severity: int) -> None:
        self.label = label
        self.code = code
        self.severity = severity

    @classmethod
    def from_prtg(cls, raw: str | None) -> Status:
        text = re.sub(
            r"[\s_-]+", "", (raw or "").lower()
        )  # PartialDown, partial_down, Partial Down
        if text in {"up", "ok"}:
            return cls.UP
        if text.startswith("warn") or text == "unusual":
            return cls.WARNING
        if text.startswith("down") or "partialdown" in text:
            return cls.DOWN  # teilweiser Ausfall (Cluster) zählt als Ausfall
        if text.startswith("pause"):
            return cls.PAUSED
        return cls.UNKNOWN


@dataclass(frozen=True)
class Device:
    id: int
    name: str
    host: str
    probe: str
    group: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sensor:
    id: int
    device_id: int
    name: str
    type: str
    status: Status
    message: str = ""
    last_check_age_s: float | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Channel:
    sensor_id: int
    id: int
    name: str
    unit: str
    value: float | None


@dataclass(frozen=True)
class Event:
    id: str | None
    time: datetime
    sensor_id: int | None
    device_id: int | None
    type: str
    status: Status
    message: str


@dataclass
class Inventory:
    devices: list[Device] = field(default_factory=list)
    sensors: list[Sensor] = field(default_factory=list)
    skipped: int = 0  # Objekte mit unbrauchbaren Angaben, die übersprungen wurden
