"""Fake NCT6791D and coretemp tree so the dashboard can run away from the board."""

from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path


def start(root: Path | None = None) -> Path:
    base = root or Path(os.environ.get("TEMP", "/tmp")) / "rampage-hwmon"
    hwmon = base / "hwmon"
    sio = hwmon / "hwmon0"
    cpu = hwmon / "hwmon1"
    sio.mkdir(parents=True, exist_ok=True)
    cpu.mkdir(parents=True, exist_ok=True)
    (sio / "name").write_text("nct6791\n", encoding="utf-8")
    (cpu / "name").write_text("coretemp\n", encoding="utf-8")

    temp_labels = {
        1: "SYSTIN",
        2: "CPUTIN",
        3: "AUXTIN0",
        4: "AUXTIN1",
        5: "PECI",
    }
    for index, label in temp_labels.items():
        (sio / f"temp{index}_label").write_text(label + "\n", encoding="utf-8")
        (sio / f"temp{index}_input").write_text("30000\n", encoding="utf-8")
    (sio / "in0_label").write_text("Vcore\n", encoding="utf-8")
    (sio / "in0_input").write_text("1050\n", encoding="utf-8")
    for index in range(1, 7):
        (sio / f"pwm{index}").write_text("80\n", encoding="utf-8")
        (sio / f"pwm{index}_enable").write_text("5\n", encoding="utf-8")
        (sio / f"pwm{index}_mode").write_text("1\n", encoding="utf-8")
        (sio / f"fan{index}_input").write_text("0\n", encoding="utf-8")
        (sio / f"fan{index}_label").write_text(f"fan{index}\n", encoding="utf-8")

    (cpu / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (cpu / "temp1_input").write_text("42000\n", encoding="utf-8")
    for core in range(6):
        (cpu / f"temp{core + 2}_label").write_text(f"Core {core}\n", encoding="utf-8")
        (cpu / f"temp{core + 2}_input").write_text("40000\n", encoding="utf-8")

    os.environ["RAMPAGE_HWMON"] = str(hwmon)

    def drift() -> None:
        started = time.time()
        while True:
            elapsed = time.time() - started
            package = 48 + math.sin(elapsed / 12) * 14
            (cpu / "temp1_input").write_text(f"{int(package * 1000)}\n", encoding="utf-8")
            for core in range(6):
                value = package - 3 + math.sin(elapsed / 9 + core) * 2
                (cpu / f"temp{core + 2}_input").write_text(f"{int(value * 1000)}\n", encoding="utf-8")
            board = 32 + math.sin(elapsed / 20) * 4
            (sio / "temp1_input").write_text(f"{int(board * 1000)}\n", encoding="utf-8")
            (sio / "temp2_input").write_text(f"{int((package - 6) * 1000)}\n", encoding="utf-8")
            (sio / "temp3_input").write_text(f"{int((board + 2) * 1000)}\n", encoding="utf-8")
            (sio / "temp4_input").write_text(f"{int((board - 1) * 1000)}\n", encoding="utf-8")
            (sio / "temp5_input").write_text(f"{int((package - 2) * 1000)}\n", encoding="utf-8")
            for index in range(1, 7):
                try:
                    raw = int((sio / f"pwm{index}").read_text(encoding="utf-8").strip() or "0")
                except (OSError, ValueError):
                    raw = 0
                rpm = int(raw / 255 * 1600)
                (sio / f"fan{index}_input").write_text(f"{rpm}\n", encoding="utf-8")
            time.sleep(1)

    threading.Thread(target=drift, name="rampage-sim", daemon=True).start()
    return hwmon
