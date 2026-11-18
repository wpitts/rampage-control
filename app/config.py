"""Persisted dashboard settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


HEADER_HINTS = [
    "CPU_FAN",
    "CPU_OPT",
    "CHA_FAN1",
    "CHA_FAN2",
    "CHA_FAN3",
    "H_AMP_FAN",
    "W_PUMP",
    "EXT_FAN",
]

PRESETS: dict[str, dict] = {
    "quiet": {
        "min_percent": 25,
        "max_percent": 100,
        "curve": [[35, 25], [50, 35], [65, 55], [78, 85]],
    },
    "balanced": {
        "min_percent": 30,
        "max_percent": 100,
        "curve": [[35, 30], [50, 45], [65, 70], [78, 100]],
    },
    "aggressive": {
        "min_percent": 40,
        "max_percent": 100,
        "curve": [[30, 45], [45, 65], [60, 85], [70, 100]],
    },
    "pump": {
        "min_percent": 70,
        "max_percent": 100,
        "curve": [[20, 80], [60, 90], [75, 100]],
    },
}


class CurvePoint(BaseModel):
    temp: float
    pwm: float

    @field_validator("pwm")
    @classmethod
    def _pwm(cls, value: float) -> float:
        if not 0 <= value <= 100:
            raise ValueError("pwm must be between 0 and 100")
        return value


class FanConfig(BaseModel):
    id: str
    label: str = ""
    enabled: bool = False
    mode: str = "monitor"
    critical: bool = False
    min_percent: int = 30
    max_percent: int = 100
    manual_percent: int = 40
    temp_source: str = ""
    curve: list[CurvePoint] = Field(
        default_factory=lambda: [
            CurvePoint(temp=35, pwm=30),
            CurvePoint(temp=50, pwm=45),
            CurvePoint(temp=65, pwm=70),
            CurvePoint(temp=78, pwm=100),
        ]
    )

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in {"monitor", "curve", "manual"}:
            raise ValueError("mode must be monitor, curve, or manual")
        return value

    @model_validator(mode="after")
    def _ranges(self) -> "FanConfig":
        if not 0 <= self.min_percent <= 100 or not 0 <= self.max_percent <= 100:
            raise ValueError("fan duty must be between 0 and 100")
        if self.min_percent > self.max_percent:
            raise ValueError("min duty cannot exceed max duty")
        if not 0 <= self.manual_percent <= 100:
            raise ValueError("manual duty must be between 0 and 100")
        if self.critical and self.min_percent < 20:
            raise ValueError("a critical header must keep at least 20% duty")
        if len(self.curve) < 2:
            raise ValueError("a curve needs at least two points")
        ordered = sorted(self.curve, key=lambda point: point.temp)
        self.curve = ordered
        return self


class GradientStop(BaseModel):
    temp: float
    color: list[int]

    @field_validator("color")
    @classmethod
    def _color(cls, value: list[int]) -> list[int]:
        if len(value) != 3 or any((not isinstance(part, int) or part < 0 or part > 255) for part in value):
            raise ValueError("color must be three integers from 0 to 255")
        return value


ZONE_MODES = {"default", "temperature", "static", "off", "alert"}
GLOBAL_MODES = {"temperature", "static", "off", "alert"}


def _check_color(value: list[int]) -> list[int]:
    if len(value) != 3 or any((not isinstance(part, int)) or part < 0 or part > 255 for part in value):
        raise ValueError("color must be three integers from 0 to 255")
    return value


class LightZone(BaseModel):
    """One addressable target: a whole controller or one of its zones."""

    id: str
    device: str
    zone: str = ""
    label: str = ""
    enabled: bool = True
    mode: str = "default"
    temp_source: str = ""
    static_color: list[int] = Field(default_factory=lambda: [255, 0, 40])
    alert_temp: float = 85

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in ZONE_MODES:
            raise ValueError("zone mode must be default, temperature, static, off, or alert")
        return value

    @field_validator("static_color")
    @classmethod
    def _static(cls, value: list[int]) -> list[int]:
        return _check_color(value)


class RgbConfig(BaseModel):
    enabled: bool = True
    launch_server: bool = True
    host: str = "127.0.0.1"
    port: int = 6742
    mode: str = "temperature"
    temp_source: str = ""
    static_color: list[int] = Field(default_factory=lambda: [255, 0, 40])
    gradient: list[GradientStop] = Field(
        default_factory=lambda: [
            GradientStop(temp=35, color=[0, 68, 255]),
            GradientStop(temp=55, color=[255, 120, 0]),
            GradientStop(temp=75, color=[255, 0, 40]),
        ]
    )
    alert_temp: float = 85
    zones: list[LightZone] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in GLOBAL_MODES:
            raise ValueError("rgb mode must be temperature, static, off, or alert")
        return value

    @field_validator("static_color")
    @classmethod
    def _static(cls, value: list[int]) -> list[int]:
        return _check_color(value)


class Settings(BaseModel):
    armed: bool = False
    poll_seconds: float = 2.0
    hysteresis_c: float = 2.0
    slew_percent_per_sec: float = 20.0
    failsafe_percent: int = 100
    fans: list[FanConfig] = Field(default_factory=list)
    rgb: RgbConfig = Field(default_factory=RgbConfig)

    @model_validator(mode="after")
    def _bounds(self) -> "Settings":
        if not 0.5 <= self.poll_seconds <= 30:
            raise ValueError("poll interval must be between 0.5 and 30 seconds")
        if not 0 <= self.hysteresis_c <= 15:
            raise ValueError("hysteresis must be between 0 and 15 C")
        if not 1 <= self.slew_percent_per_sec <= 100:
            raise ValueError("slew rate must be between 1 and 100 percent per second")
        if not 50 <= self.failsafe_percent <= 100:
            raise ValueError("failsafe duty must be between 50 and 100 percent")
        return self


def config_path() -> Path:
    override = os.environ.get("RAMPAGE_CONFIG", "").strip()
    if override:
        return Path(override)
    return Path("/config/config.json")


def default_settings() -> Settings:
    armed = os.environ.get("CONTROL_ARMED", "").strip().lower() in {"1", "true", "yes"}
    return Settings(armed=armed)


def load_settings() -> Settings:
    path = config_path()
    if not path.is_file():
        return default_settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Settings.model_validate(data)


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump_json(indent=2)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(path)
