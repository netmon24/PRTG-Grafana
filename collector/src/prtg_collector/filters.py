"""Abruffilter aus cores.yaml. Mehrere Filterarten wirken zusammen (UND), Werte je Art ODER."""

from __future__ import annotations

import re

from .config import Filters
from .models import Inventory


def apply_filters(inv: Inventory, f: Filters) -> Inventory:
    if not (f.tags or f.groups or f.sensor_types or f.name_regex):
        return inv
    pattern = re.compile(f.name_regex) if f.name_regex else None
    devices = {d.id: d for d in inv.devices}
    keep = []
    for s in inv.sensors:
        dev = devices.get(s.device_id)
        if dev is None:
            continue
        if f.tags and not set(f.tags) & (set(s.tags) | set(dev.tags)):
            continue
        if f.groups and dev.group not in f.groups:
            continue
        if f.sensor_types and s.type not in f.sensor_types:
            continue
        if pattern and not pattern.search(s.name):
            continue
        keep.append(s)
    used = {s.device_id for s in keep}
    return Inventory(devices=[d for d in inv.devices if d.id in used], sensors=keep)
