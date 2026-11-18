import os
import struct
import tempfile
import unittest
from pathlib import Path

from app.curves import decide_pwm, interpolate, mix_color
from app.disks import parse_disks_ini
from app.hwmon import apply_pwm, read_int, scan
from app.rgb import parse_controller_data


def _cstr(text: str) -> bytes:
    raw = text.encode() + b"\x00"
    return struct.pack("<H", len(raw)) + raw


class CurveTests(unittest.TestCase):
    def test_interpolate_clamps_and_ramps(self):
        curve = [(30, 20), (50, 40), (70, 100)]
        self.assertEqual(interpolate(curve, 20), 20)
        self.assertEqual(interpolate(curve, 40), 30)
        self.assertEqual(interpolate(curve, 80), 100)

    def test_hysteresis_holds_while_cooling(self):
        curve = [(30, 20), (70, 100)]
        decision, anchor, output = decide_pwm(
            armed=True,
            enabled=True,
            mode="curve",
            min_percent=20,
            max_percent=100,
            manual_percent=40,
            curve=curve,
            temp=69,
            temp_state="ok",
            hysteresis_c=2,
            anchor_temp=70,
            last_output=100,
            slew_step=100,
            failsafe_percent=100,
            override=None,
            resume_from=None,
        )
        self.assertEqual(decision.reason, "hold")
        self.assertEqual(output, 100)
        self.assertEqual(anchor, 70)

    def test_missing_sensor_fails_safe_above_the_max_duty(self):
        decision, _anchor, output = decide_pwm(
            armed=True,
            enabled=True,
            mode="curve",
            min_percent=30,
            max_percent=60,
            manual_percent=40,
            curve=[(30, 30), (70, 60)],
            temp=None,
            temp_state="missing",
            hysteresis_c=2,
            anchor_temp=None,
            last_output=40,
            slew_step=5,
            failsafe_percent=100,
            override=None,
            resume_from=None,
        )
        self.assertEqual(decision.reason, "failsafe")
        self.assertEqual(output, 100)
        self.assertTrue(decision.write)

    def test_spun_down_disk_uses_the_minimum(self):
        decision, _anchor, output = decide_pwm(
            armed=True,
            enabled=True,
            mode="curve",
            min_percent=25,
            max_percent=100,
            manual_percent=40,
            curve=[(30, 80), (70, 100)],
            temp=None,
            temp_state="spun_down",
            hysteresis_c=2,
            anchor_temp=40,
            last_output=80,
            slew_step=100,
            failsafe_percent=100,
            override=None,
            resume_from=None,
        )
        self.assertEqual(decision.reason, "spun-down")
        self.assertEqual(output, 25)

    def test_monitor_does_not_write(self):
        decision, _anchor, _output = decide_pwm(
            armed=True,
            enabled=True,
            mode="monitor",
            min_percent=30,
            max_percent=100,
            manual_percent=40,
            curve=[(30, 30), (70, 100)],
            temp=80,
            temp_state="ok",
            hysteresis_c=0,
            anchor_temp=None,
            last_output=None,
            slew_step=100,
            failsafe_percent=100,
            override=None,
            resume_from=None,
        )
        self.assertFalse(decision.write)

    def test_color_mix(self):
        color = mix_color([(0, (0, 0, 0)), (10, (10, 0, 0))], 5)
        self.assertEqual(color, (5, 0, 0))


class DiskTests(unittest.TestCase):
    def test_parse_ignores_spun_down_and_flash(self):
        text = """
["flash"]
device="sda"
type="Flash"
temperature="*"
["disk1"]
name="disk1"
device="sdb"
temperature="31"
["disk2"]
name="disk2"
device="sdc"
temperature="*"
"""
        disks = parse_disks_ini(text)
        self.assertEqual([disk.id for disk in disks], ["disk:sdb", "disk:sdc"])
        self.assertEqual(disks[0].celsius, 31)
        self.assertTrue(disks[1].spun_down)


class HwmonTests(unittest.TestCase):
    def test_scan_and_manual_pwm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chip = root / "hwmon0"
            chip.mkdir()
            (chip / "name").write_text("nct6791\n", encoding="utf-8")
            (chip / "temp1_input").write_text("47000\n", encoding="utf-8")
            (chip / "temp1_label").write_text("SYSTIN\n", encoding="utf-8")
            (chip / "pwm1").write_text("10\n", encoding="utf-8")
            (chip / "pwm1_enable").write_text("5\n", encoding="utf-8")
            (chip / "fan1_input").write_text("400\n", encoding="utf-8")
            found = scan(root)
            self.assertEqual(len(found.pwms), 1)
            self.assertEqual(found.temps[0].label, "SYSTIN")
            apply_pwm(found.pwms[0], 50)
            self.assertEqual(read_int(found.pwms[0].enable_path), 1)
            self.assertEqual(read_int(found.pwms[0].pwm_path), round(0.50 * 255))


class RgbProtocolTests(unittest.TestCase):
    def test_parse_aura_controller(self):
        mode = _cstr("Direct") + struct.pack("<i11IH", 0, *([0] * 11), 0)
        zone = _cstr("RGB Header") + struct.pack("<iIIIHH", 1, 0, 1, 1, 0, 0)
        body = b"".join(
            [
                struct.pack("<i", 0),
                _cstr("ASUS Aura"),
                _cstr("ASUS"),
                _cstr("Rampage V Edition 10"),
                _cstr("1"),
                _cstr(""),
                _cstr("SMBus"),
                struct.pack("<Hi", 1, 0),
                mode,
                struct.pack("<H", 1),
                zone,
                struct.pack("<H", 4),
            ]
        )
        payload = struct.pack("<I", len(body) + 4) + body
        device = parse_controller_data(payload, 4)
        self.assertEqual(device.name, "ASUS Aura")
        self.assertEqual(device.vendor, "ASUS")
        self.assertEqual(device.led_count, 4)
        self.assertEqual(device.zones[0].name, "RGB Header")
        self.assertEqual(device.zones[0].leds, 1)


class ZoneLightingTests(unittest.TestCase):
    def _devices(self):
        from app.rgb import DeviceInfo, ZoneInfo

        return [
            DeviceInfo(0, "ASUS Aura", "ASUS", "mb", 2, [ZoneInfo("I/O", 1), ZoneInfo("PCIe", 1)]),
            DeviceInfo(1, "G.Skill Trident Z RGB", "G.Skill", "A1", 5, [ZoneInfo("Module", 5)]),
            DeviceInfo(2, "G.Skill Trident Z RGB", "G.Skill", "B1", 5, [ZoneInfo("Module", 5)]),
        ]

    def test_enumerate_disambiguates_duplicate_devices(self):
        from app.engine import enumerate_fixtures

        fixtures = enumerate_fixtures(self._devices())
        ids = [fixture.id for fixture in fixtures]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("G.Skill Trident Z RGB@0/z0:Module", ids)
        self.assertIn("G.Skill Trident Z RGB@1/z0:Module", ids)
        self.assertEqual(fixtures[0].id, "ASUS Aura@0/z0:I/O")

    def test_zone_override_beats_global_default(self):
        from app.config import LightZone, Settings

        settings = Settings()
        settings.rgb.mode = "static"
        settings.rgb.static_color = [10, 20, 30]
        lookup = {"a:temp1": (50.0, "ok")}

        from app.engine import Engine

        engine = Engine.__new__(Engine)  # no __init__; _resolve_zone only reads settings
        # inherit default
        enabled, color = engine._resolve_zone(settings, LightZone(id="x", device="d", mode="default"), lookup)
        self.assertTrue(enabled)
        self.assertEqual(color, (10, 20, 30))
        # zone static override
        zone = LightZone(id="y", device="d", mode="static", static_color=[1, 2, 3])
        enabled, color = engine._resolve_zone(settings, zone, lookup)
        self.assertEqual(color, (1, 2, 3))
        # disabled zone
        off = LightZone(id="z", device="d", enabled=False)
        enabled, _ = engine._resolve_zone(settings, off, lookup)
        self.assertFalse(enabled)

    def test_update_zone_packet(self):
        from app.rgb import MAGIC, UPDATE_ZONE_LEDS, DeviceInfo, OpenRGBClient, ZoneInfo

        sent: list[bytes] = []

        class FakeSocket:
            def sendall(self, data):
                sent.append(data)

        client = OpenRGBClient("127.0.0.1", 6742)
        client._socket = FakeSocket()
        client.devices = [DeviceInfo(0, "d", "v", "", 3, [ZoneInfo("Strip", 3)])]
        client.update_zone(0, 0, 3, (255, 128, 64))

        # last packet is UPDATE_ZONE_LEDS
        packet = sent[-1]
        magic, device_id, packet_id, size = struct.unpack("<4sIII", packet[:16])
        self.assertEqual(magic, MAGIC)
        self.assertEqual(packet_id, UPDATE_ZONE_LEDS)
        payload = packet[16:]
        data_size, zone_index, count = struct.unpack("<IIH", payload[:10])
        self.assertEqual(zone_index, 0)
        self.assertEqual(count, 3)
        self.assertEqual(payload[10:14], struct.pack("<BBBB", 255, 128, 64, 0))


class EngineTests(unittest.TestCase):
    def test_curve_writes_only_when_armed(self):
        from app.engine import Engine

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            hwmon = root / "hwmon"
            chip = hwmon / "hwmon0"
            cpu = hwmon / "hwmon1"
            chip.mkdir(parents=True)
            cpu.mkdir()
            (chip / "name").write_text("nct6791\n", encoding="utf-8")
            (chip / "temp1_label").write_text("SYSTIN\n", encoding="utf-8")
            (chip / "temp1_input").write_text("30000\n", encoding="utf-8")
            (chip / "pwm1").write_text("40\n", encoding="utf-8")
            (chip / "pwm1_enable").write_text("5\n", encoding="utf-8")
            (chip / "fan1_input").write_text("500\n", encoding="utf-8")
            (cpu / "name").write_text("coretemp\n", encoding="utf-8")
            (cpu / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
            (cpu / "temp1_input").write_text("80000\n", encoding="utf-8")
            os.environ["RAMPAGE_CONFIG"] = str(config)
            os.environ["RAMPAGE_HWMON"] = str(hwmon)
            os.environ.pop("RAMPAGE_SIMULATE", None)
            engine = Engine()
            engine.simulated = True
            engine.settings.rgb.enabled = False
            engine._tick()
            fan = engine.settings.fans[0]
            fan.enabled = True
            fan.mode = "curve"
            fan.temp_source = next(sensor.id for sensor in engine.sensors if sensor.label == "Package id 0")
            engine.settings.armed = False
            engine._tick()
            self.assertEqual(read_int(chip / "pwm1_enable"), 5)
            self.assertEqual(read_int(chip / "pwm1"), 40)
            engine.settings.armed = True
            engine._tick()
            self.assertEqual(read_int(chip / "pwm1_enable"), 1)
            self.assertEqual(read_int(chip / "pwm1"), 255)


if __name__ == "__main__":
    unittest.main()
