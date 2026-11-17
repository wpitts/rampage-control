"""Unraid disks.ini temperatures. Spun-down disks stay asleep."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiskTemp:
    id: str
    label: str
    celsius: float | None
    spun_down: bool


def disks_path() -> Path | None:
    override = os.environ.get("RAMPAGE_DISKS", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.extend(
        [
            Path("/unraid/disks.ini"),
            Path("/var/local/emhttp/disks.ini"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def parse_disks_ini(text: str) -> list[DiskTemp]:
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().strip('"')
            current = {"section": name}
            sections.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip().strip('"')

    disks: list[DiskTemp] = []
    for section in sections:
        device = section.get("device") or section.get("section") or ""
        if not device or "temperature" not in section:
            continue
        if section.get("type", "").lower() in {"flash", "rom"}:
            continue
        raw_temp = section.get("temperature", "")
        spun_down = raw_temp in {"", "*", "N/A", "n/a", "-"}
        celsius: float | None = None
        if not spun_down:
            try:
                celsius = float(raw_temp)
            except ValueError:
                spun_down = True
        label = section.get("name") or section.get("section") or device
        disks.append(
            DiskTemp(
                id=f"disk:{device}",
                label=label,
                celsius=celsius,
                spun_down=spun_down or celsius is None,
            )
        )
    return disks


def load_disks() -> list[DiskTemp]:
    path = disks_path()
    if path is None:
        return []
    try:
        return parse_disks_ini(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
