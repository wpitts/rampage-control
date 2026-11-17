"""Read and write Linux hwmon sysfs entries."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TempChannel:
    id: str
    chip: str
    label: str
    path: Path


@dataclass
class VoltageChannel:
    id: str
    chip: str
    label: str
    path: Path


@dataclass
class RpmChannel:
    id: str
    chip: str
    label: str
    path: Path


@dataclass
class PwmChannel:
    id: str
    chip: str
    index: int
    label: str
    pwm_path: Path
    enable_path: Path | None
    mode_path: Path | None
    rpm_path: Path | None


@dataclass
class Hardware:
    temps: list[TempChannel]
    voltages: list[VoltageChannel]
    rpms: list[RpmChannel]
    pwms: list[PwmChannel]


def hwmon_root() -> Path:
    override = os.environ.get("RAMPAGE_HWMON", "").strip()
    if override:
        return Path(override)
    return Path("/sys/class/hwmon")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _chip_key(hwmon: Path) -> str:
    try:
        link = os.readlink(hwmon)
    except OSError:
        link = ""
    match = re.search(r"([^/\\]+)[/\\]hwmon[/\\]hwmon\d+$", link)
    if match:
        return match.group(1)
    name = _read_text(hwmon / "name") or "hwmon"
    return f"{name}@{hwmon.name}"


def _label(hwmon: Path, stem: str, fallback: str) -> str:
    text = _read_text(hwmon / f"{stem}_label")
    return text if text else fallback


def scan(root: Path | None = None) -> Hardware:
    base = root or hwmon_root()
    temps: list[TempChannel] = []
    voltages: list[VoltageChannel] = []
    rpms: list[RpmChannel] = []
    pwms: list[PwmChannel] = []
    if not base.is_dir():
        return Hardware(temps, voltages, rpms, pwms)

    claimed_rpm: set[Path] = set()
    for hwmon in sorted(path for path in base.iterdir() if path.is_dir()):
        chip = _chip_key(hwmon)
        for path in sorted(hwmon.glob("temp*_input")):
            index = path.name.removeprefix("temp").removesuffix("_input")
            temps.append(
                TempChannel(
                    id=f"{chip}:temp{index}",
                    chip=chip,
                    label=_label(hwmon, f"temp{index}", f"temp{index}"),
                    path=path,
                )
            )
        for path in sorted(hwmon.glob("in*_input")):
            index = path.name.removeprefix("in").removesuffix("_input")
            voltages.append(
                VoltageChannel(
                    id=f"{chip}:in{index}",
                    chip=chip,
                    label=_label(hwmon, f"in{index}", f"in{index}"),
                    path=path,
                )
            )
        for path in sorted(hwmon.glob("pwm[0-9]*")):
            if not re.fullmatch(r"pwm\d+", path.name):
                continue
            index = int(path.name.removeprefix("pwm"))
            rpm_path = hwmon / f"fan{index}_input"
            enable_path = hwmon / f"pwm{index}_enable"
            mode_path = hwmon / f"pwm{index}_mode"
            if rpm_path.is_file():
                claimed_rpm.add(rpm_path)
            pwms.append(
                PwmChannel(
                    id=f"{chip}:pwm{index}",
                    chip=chip,
                    index=index,
                    label=_label(hwmon, f"fan{index}", f"pwm{index}"),
                    pwm_path=path,
                    enable_path=enable_path if enable_path.is_file() else None,
                    mode_path=mode_path if mode_path.is_file() else None,
                    rpm_path=rpm_path if rpm_path.is_file() else None,
                )
            )
        for path in sorted(hwmon.glob("fan*_input")):
            if path in claimed_rpm:
                continue
            index = path.name.removeprefix("fan").removesuffix("_input")
            rpms.append(
                RpmChannel(
                    id=f"{chip}:fan{index}",
                    chip=chip,
                    label=_label(hwmon, f"fan{index}", f"fan{index}"),
                    path=path,
                )
            )
    return Hardware(temps, voltages, rpms, pwms)


def read_milli(path: Path | None, scale: float) -> float | None:
    if path is None:
        return None
    text = _read_text(path)
    if text is None or text == "":
        return None
    try:
        return int(text) / scale
    except ValueError:
        return None


def read_int(path: Path | None) -> int | None:
    if path is None:
        return None
    text = _read_text(path)
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def apply_pwm(channel: PwmChannel, percent: float) -> None:
    raw = int(round(max(0.0, min(100.0, percent)) / 100.0 * 255.0))
    if channel.enable_path is not None:
        current = read_int(channel.enable_path)
        if current != 1:
            write_text(channel.enable_path, "1\n")
    if read_int(channel.pwm_path) != raw:
        write_text(channel.pwm_path, f"{raw}\n")
