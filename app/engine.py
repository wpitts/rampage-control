"""Background sampler, fan writer, and lighting updater."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from app.config import FanConfig, Settings, load_settings, save_settings
from app.curves import Decision, decide_pwm, mix_color
from app.disks import DiskTemp, load_disks
from app.hwmon import Hardware, PwmChannel, apply_pwm, read_int, read_milli, scan
from app.rgb import OpenRGBClient

log = logging.getLogger("rampage")

AUTO_RGB_HINTS = ("asus", "aura", "rampage", "ene")


@dataclass
class SensorView:
    id: str
    label: str
    kind: str
    value: float | None
    unit: str
    state: str
    chip: str


@dataclass
class FanRuntime:
    anchor_temp: float | None = None
    last_output: float | None = None
    override_until: float = 0.0
    override_percent: float = 100.0
    restore_percent: float | None = None
    was_identifying: bool = False
    last_error: str = ""
    reason: str = "monitor"


@dataclass
class RgbView:
    connected: bool = False
    simulated: bool = False
    error: str = ""
    color: list[int] = field(default_factory=lambda: [0, 0, 0])
    devices: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


class Engine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.settings = load_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.simulated = os.environ.get("RAMPAGE_SIMULATE", "").strip().lower() in {"1", "true", "yes"}
        self.sensors: list[SensorView] = []
        self.fans: list[dict] = []
        self.warnings: list[str] = []
        self.rgb = RgbView(simulated=self.simulated)
        self.updated = 0.0
        self._runtime: dict[str, FanRuntime] = {}
        self._history: deque[dict] = deque(maxlen=900)
        self._rgb_client: OpenRGBClient | None = None
        self._rgb_next = 0.0
        self._last_color: tuple[int, int, int] | None = None
        self._openrgb: subprocess.Popen | None = None
        self._openrgb_log: deque[str] = deque(maxlen=30)
        self._sim_devices = [
            {
                "index": 0,
                "name": "Simulated Aura (no hardware)",
                "vendor": "preview",
                "description": "Stand-in for the Rampage V Aura zones",
                "leds": 6,
                "zones": ["IO shield", "Audio", "PCIe", "Center ROG", "Rear edge", "RGB header"],
            }
        ]

    def start(self) -> None:
        if self.simulated:
            from app.simulate import start as start_sim

            start_sim()
            self.rgb.devices = self._sim_devices
            self.rgb.connected = True
        self._thread = threading.Thread(target=self._loop, name="rampage-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._rgb_client is not None:
            self._rgb_client.close()
        if self._openrgb is not None and self._openrgb.poll() is None:
            self._openrgb.terminate()

    def snapshot(self) -> dict:
        with self._lock:
            trends = self._trends()
            return {
                "board": "ROG Rampage V Edition 10",
                "version": "1.0.0",
                "simulated": self.simulated,
                "armed": self.settings.armed,
                "updated": self.updated,
                "warnings": list(self.warnings),
                "sensors": [sensor.__dict__ for sensor in self.sensors],
                "fans": self.fans,
                "rgb": {
                    "connected": self.rgb.connected,
                    "simulated": self.rgb.simulated,
                    "error": self.rgb.error,
                    "color": list(self.rgb.color),
                    "devices": self.rgb.devices,
                    "log": list(self.rgb.log),
                    "mode": self.settings.rgb.mode,
                },
                "trends": trends,
            }

    def get_settings(self) -> Settings:
        with self._lock:
            return self.settings.model_copy(deep=True)

    def replace_settings(self, settings: Settings) -> Settings:
        with self._lock:
            self.settings = settings
            save_settings(settings)
            return settings.model_copy(deep=True)

    def set_armed(self, armed: bool) -> Settings:
        with self._lock:
            self.settings.armed = armed
            save_settings(self.settings)
            return self.settings.model_copy(deep=True)

    def pulse(self, fan_id: str, percent: float = 100, seconds: float = 3) -> None:
        with self._lock:
            if not self.settings.armed:
                raise PermissionError("Arm fan control before identifying a header.")
            known = {fan.id for fan in self.settings.fans}
            if fan_id not in known:
                raise KeyError(fan_id)
            runtime = self._runtime.setdefault(fan_id, FanRuntime())
            runtime.override_percent = max(0.0, min(100.0, percent))
            runtime.override_until = time.time() + seconds

    def _trends(self) -> dict:
        temps: dict[str, list[float | None]] = {}
        duties: dict[str, list[float | None]] = {}
        for sample in list(self._history)[-90:]:
            for key, value in sample.get("temps", {}).items():
                temps.setdefault(key, []).append(value)
            for key, value in sample.get("duties", {}).items():
                duties.setdefault(key, []).append(value)
        return {"temps": temps, "duties": duties}

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._tick()
            except Exception:
                log.exception("control tick failed")
            with self._lock:
                delay = self.settings.poll_seconds
            remaining = delay - (time.time() - started)
            if remaining > 0:
                self._stop.wait(remaining)

    def _tick(self) -> None:
        hardware = scan()
        disks = load_disks()
        sensors, lookup = self._collect(hardware, disks)
        with self._lock:
            self._merge_fans(self.settings, hardware, lookup)
            settings = self.settings.model_copy(deep=True)
        fan_views = self._control(settings, hardware, lookup)
        rgb_state = self._lighting(settings, lookup)
        warnings = self._warnings(hardware, sensors)
        now = time.time()
        temp_trend = {sensor.id: sensor.value for sensor in sensors if sensor.kind == "temp"}
        duty_trend = {fan["id"]: fan["pwm_percent"] for fan in fan_views}
        with self._lock:
            self.sensors = sensors
            self.fans = fan_views
            self.warnings = warnings
            self.updated = now
            self.rgb = rgb_state
            self._history.append({"temps": temp_trend, "duties": duty_trend})

    def _collect(self, hardware: Hardware, disks: list[DiskTemp]) -> tuple[list[SensorView], dict]:
        sensors: list[SensorView] = []
        lookup: dict[str, tuple[float | None, str]] = {}
        for temp in hardware.temps:
            value = read_milli(temp.path, 1000)
            state = "ok" if value is not None else "error"
            sensors.append(SensorView(temp.id, temp.label, "temp", value, "C", state, temp.chip))
            lookup[temp.id] = (value, state)
        for voltage in hardware.voltages:
            raw = read_milli(voltage.path, 1000)
            sensors.append(
                SensorView(voltage.id, voltage.label, "voltage", raw, "V", "ok" if raw is not None else "error", voltage.chip)
            )
        for rpm in hardware.rpms:
            value = read_milli(rpm.path, 1)
            sensors.append(SensorView(rpm.id, rpm.label, "rpm", value, "rpm", "ok" if value is not None else "error", rpm.chip))
        hottest: float | None = None
        any_disk = False
        any_awake = False
        for disk in disks:
            any_disk = True
            if disk.spun_down or disk.celsius is None:
                sensors.append(SensorView(disk.id, disk.label, "temp", None, "C", "spun_down", "array"))
                lookup[disk.id] = (None, "spun_down")
            else:
                any_awake = True
                sensors.append(SensorView(disk.id, disk.label, "temp", disk.celsius, "C", "ok", "array"))
                lookup[disk.id] = (disk.celsius, "ok")
                hottest = disk.celsius if hottest is None else max(hottest, disk.celsius)
        if any_disk:
            if any_awake and hottest is not None:
                sensors.insert(0, SensorView("array:hottest", "Hottest disk", "temp", hottest, "C", "ok", "array"))
                lookup["array:hottest"] = (hottest, "ok")
            else:
                sensors.insert(0, SensorView("array:hottest", "Hottest disk", "temp", None, "C", "spun_down", "array"))
                lookup["array:hottest"] = (None, "spun_down")
        return sensors, lookup

    def _merge_fans(self, settings: Settings, hardware: Hardware, lookup: dict) -> None:
        by_id = {fan.id: fan for fan in settings.fans}
        default_source = _preferred_source(lookup)
        changed = False
        for channel in hardware.pwms:
            if channel.id in by_id:
                continue
            settings.fans.append(
                FanConfig(
                    id=channel.id,
                    label=channel.label if channel.label != f"pwm{channel.index}" else f"Header {channel.index}",
                    enabled=False,
                    mode="monitor",
                    critical=channel.index == 1 and "nct" in channel.chip.lower(),
                    temp_source=default_source,
                )
            )
            changed = True
        if changed:
            try:
                save_settings(settings)
            except OSError:
                log.warning("could not persist a newly discovered fan header")

    def _control(self, settings: Settings, hardware: Hardware, lookup: dict) -> list[dict]:
        channels = {channel.id: channel for channel in hardware.pwms}
        views = []
        step = settings.slew_percent_per_sec * settings.poll_seconds
        now = time.time()
        for fan in settings.fans:
            runtime = self._runtime.setdefault(fan.id, FanRuntime())
            channel = channels.get(fan.id)
            rpm = read_milli(channel.rpm_path, 1) if channel else None
            readback = read_int(channel.pwm_path) if channel else None
            read_percent = None if readback is None else round(readback / 255 * 100, 1)
            temp, state = _source(lookup, fan.temp_source)
            identifying = runtime.override_until > now
            resume = None
            if identifying:
                if not runtime.was_identifying:
                    runtime.restore_percent = read_percent if read_percent is not None else runtime.last_output
                override = runtime.override_percent
                runtime.was_identifying = True
            else:
                override = None
                if runtime.was_identifying:
                    resume = runtime.override_percent
                runtime.was_identifying = False
            decision, anchor, output = decide_pwm(
                armed=settings.armed,
                enabled=fan.enabled,
                mode=fan.mode,
                min_percent=fan.min_percent,
                max_percent=fan.max_percent,
                manual_percent=fan.manual_percent,
                curve=[(point.temp, point.pwm) for point in fan.curve],
                temp=temp,
                temp_state=state,
                hysteresis_c=settings.hysteresis_c,
                anchor_temp=runtime.anchor_temp,
                last_output=runtime.last_output,
                slew_step=step,
                failsafe_percent=settings.failsafe_percent,
                override=override,
                resume_from=resume,
            )
            if resume is not None and not decision.write and runtime.restore_percent is not None:
                decision = Decision(float(runtime.restore_percent), "restore", True)
                output = decision.percent
            if not identifying:
                runtime.restore_percent = None
            runtime.anchor_temp = anchor
            runtime.last_output = output
            error = ""
            if decision.write and channel is not None:
                try:
                    apply_pwm(channel, decision.percent)
                except OSError as exc:
                    error = str(exc)
                    log.warning("pwm write failed for %s: %s", fan.id, exc)
            elif decision.write and channel is None:
                error = "header is not present on this boot"
            runtime.last_error = error
            runtime.reason = decision.reason
            note = ""
            if rpm == 0 and (read_percent or 0) > 20:
                note = "No tach signal. The header may be empty or the fan has no speed wire."
            views.append(
                {
                    "id": fan.id,
                    "label": fan.label,
                    "present": channel is not None,
                    "rpm": rpm,
                    "pwm_percent": read_percent,
                    "target_percent": round(decision.percent, 1) if decision.write else None,
                    "reason": decision.reason,
                    "temp": temp,
                    "temp_state": state,
                    "mode": fan.mode,
                    "enabled": fan.enabled,
                    "critical": fan.critical,
                    "error": error,
                    "note": note,
                }
            )
        return views

    def _lighting(self, settings: Settings, lookup: dict) -> RgbView:
        state = RgbView(simulated=self.simulated, log=list(self._openrgb_log))
        if not settings.rgb.enabled:
            state.error = "Lighting control is off."
            return state
        if self.simulated:
            color = self._color_for(settings, lookup)
            state.connected = True
            state.error = ""
            state.color = list(color)
            state.devices = self._sim_devices
            return state
        self._ensure_openrgb(settings)
        state.log = list(self._openrgb_log)
        now = time.time()
        if self._rgb_client is None:
            if now < self._rgb_next:
                state.error = self.rgb.error
                return state
            self._rgb_next = now + 10
            try:
                client = OpenRGBClient(settings.rgb.host, settings.rgb.port)
                client.connect()
                self._rgb_client = client
                self._last_color = None
            except OSError as exc:
                state.error = f"OpenRGB is not reachable at {settings.rgb.host}:{settings.rgb.port}. {exc}"
                return state
        client = self._rgb_client
        if client is None:
            return state
        try:
            color = self._color_for(settings, lookup)
            if color != self._last_color:
                indexes = self._rgb_targets(settings, client)
                if indexes:
                    client.set_color(indexes, color)
                self._last_color = color
            state.connected = True
            state.color = list(color)
            state.devices = [
                {
                    "index": device.index,
                    "name": device.name,
                    "vendor": device.vendor,
                    "description": device.description,
                    "leds": device.led_count,
                    "zones": [zone.name for zone in device.zones],
                }
                for device in client.devices
            ]
            if not state.devices:
                state.error = "OpenRGB is running and has not found a lighting controller."
            elif not self._rgb_targets(settings, client):
                state.error = "No Aura device is selected. Choose a controller below."
        except Exception as exc:
            log.warning("rgb update failed: %s", exc)
            state.connected = False
            state.error = str(exc)
            client.close()
            self._rgb_client = None
            self._last_color = None
        return state

    def _color_for(self, settings: Settings, lookup: dict) -> tuple[int, int, int]:
        rgb = settings.rgb
        if rgb.mode == "off":
            return (0, 0, 0)
        temp, state = _source(lookup, rgb.temp_source or _preferred_source(lookup))
        if rgb.mode == "static":
            color = tuple(rgb.static_color)
        elif rgb.mode == "alert":
            hot = state == "ok" and temp is not None and temp >= rgb.alert_temp
            color = (255, 0, 40) if hot else tuple(rgb.static_color)
        else:
            if state == "ok" and temp is not None:
                stops = [(stop.temp, tuple(stop.color)) for stop in rgb.gradient]
                color = mix_color(stops, temp)
            else:
                color = tuple(rgb.static_color)
        hot = state == "ok" and temp is not None and temp >= rgb.alert_temp
        if hot and int(time.time()) % 2 == 0 and rgb.mode in {"temperature", "alert"}:
            return (255, 255, 255)
        return color  # type: ignore[return-value]

    def _rgb_targets(self, settings: Settings, client: OpenRGBClient) -> list[int]:
        chosen = settings.rgb.device_indexes
        if chosen is not None:
            return list(chosen)
        matched = [
            device.index
            for device in client.devices
            if any(hint in f"{device.vendor} {device.name} {device.description}".lower() for hint in AUTO_RGB_HINTS)
        ]
        if matched:
            return matched
        if len(client.devices) == 1:
            return [client.devices[0].index]
        return []

    def _ensure_openrgb(self, settings: Settings) -> None:
        if not settings.rgb.launch_server or settings.rgb.host not in {"127.0.0.1", "localhost"}:
            return
        if self._openrgb is not None and self._openrgb.poll() is None:
            return
        binary = shutil.which("openrgb")
        if not binary:
            self._openrgb_log.append("openrgb is not installed in this container")
            return
        try:
            process = subprocess.Popen(
                [binary, "--server", "--server-port", str(settings.rgb.port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={**os.environ, "HOME": "/config", "QT_QPA_PLATFORM": "offscreen"},
            )
        except OSError as exc:
            self._openrgb_log.append(str(exc))
            return
        self._openrgb = process

        def drain() -> None:
            if process.stdout is None:
                return
            for line in process.stdout:
                self._openrgb_log.append(line.rstrip())

        threading.Thread(target=drain, name="openrgb-log", daemon=True).start()

    def _warnings(self, hardware: Hardware, sensors: list[SensorView]) -> list[str]:
        warnings = []
        chips = {item.chip.lower() for item in hardware.temps}
        chips.update(channel.chip.lower() for channel in hardware.pwms)
        if not self.simulated and not any("nct6791" in chip or "nct6775" in chip for chip in chips):
            warnings.append(
                "The NCT6791D super I/O was not found. On the Unraid host, load nct6775 before starting this container."
            )
        if not self.simulated and not any("coretemp" in chip for chip in chips):
            warnings.append("coretemp was not found, so the CPU package temperature is unavailable. Load the coretemp module.")
        if not hardware.pwms and not self.simulated:
            warnings.append("No PWM headers were found. Fan curves can be edited, and they will apply when a header appears.")
        unwritable = False
        for channel in hardware.pwms:
            if not os.access(channel.pwm_path, os.W_OK):
                unwritable = True
                break
        if unwritable and not self.simulated:
            warnings.append("PWM files are not writable. Run the container privileged with /sys mounted read-write.")
        if any(sensor.state == "error" and sensor.kind == "temp" for sensor in sensors):
            warnings.append("At least one temperature file could not be read.")
        return warnings


def _preferred_source(lookup: dict) -> str:
    for key in lookup:
        if key.endswith(":temp1") and "coretemp" in key:
            return key
    for key in lookup:
        if "coretemp" in key:
            return key
    for key, (_value, state) in lookup.items():
        if state == "ok" and ":temp" in key:
            return key
    return ""


def _source(lookup: dict, source: str) -> tuple[float | None, str]:
    if not source:
        return None, "missing"
    found = lookup.get(source)
    if found is None:
        return None, "missing"
    return found
