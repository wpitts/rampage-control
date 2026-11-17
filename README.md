# Rampage Control

Dashboard and control loop for an Unraid server built on the [ASUS ROG Rampage V Edition 10](https://rog.asus.com/motherboards/rog-rampage/rog-rampage-v-edition-10-model/).

It reads CPU, motherboard, extension-card, and array temperatures, then applies a curve to each fan header. Aura color can follow the same temperatures when OpenRGB can see a lighting controller.

Fan writes stay off until you arm control in the dashboard.

## What the board exposes

| Hardware | How the app uses it |
| --- | --- |
| Nuvoton NCT6791D | Temperatures, voltages, RPM, and PWM through the Linux `nct6775` driver |
| CPU package and cores | `coretemp` |
| CPU_FAN, CPU_OPT, CHA_FAN1–3, H_AMP_FAN, W_PUMP | Whichever PWM files the driver publishes. The kernel does not use the silkscreen names, so identify each header before enabling it |
| EXT_FAN and the fan extension card | Extra headers and thermistors when the kernel publishes them |
| Array disks | `/var/local/emhttp/disks.ini`, including spun-down disks, without waking them |
| 12 V Aura header and onboard zones | OpenRGB. The header is analog 12 V G/R/B, 2 A, 2 m. It is not a 5 V addressable header |

H_AMP is the high-current header (about 3 A). Keep a high minimum on H_AMP and on W_PUMP.

Motherboard Aura is on the Super I/O SMBus. The stock Unraid kernel does not provide that bus (`i2c-nct6775`), so onboard LEDs and the 12 V header may not appear. USB lighting devices still work. Fan control does not need that bus.

If `nct6791` is missing from the dashboard after the module loads, the chipset may be reserving the Super I/O. The lm-sensors remedy is the kernel argument `acpi_enforce_resources=lax` in the Unraid syslinux config. That relaxes ACPI resource checks. Use it only when the chip stays hidden.

## Install on Unraid

1. Copy this directory to the server, for example `/mnt/user/appdata/rampage-control-src`.
2. Build the image:

```bash
cd /mnt/user/appdata/rampage-control-src
docker build -t local/rampage-control:latest .
```

3. Install the template:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
cp unraid/rampage-control.xml /boot/config/plugins/dockerMan/templates-user/my-rampage-control.xml
```

4. Add the module lines in `scripts/unraid-go.sh` to `/boot/config/go`, then load them once from the Unraid console:

```bash
modprobe nct6775
modprobe coretemp
modprobe drivetemp
modprobe i2c-dev
modprobe i2c-i801
```

5. In the Docker tab, add the container from the **rampage-control** template. Leave it privileged. Keep the `/sys`, `/dev`, and appdata mappings. Do not expose port 8787 to the internet. Set `RAMPAGE_TOKEN` if the LAN is shared.
6. Open `http://<tower>:8787`.

## First run

1. Confirm the chip pill says the NCT6791D is visible and that package temperature is listed.
2. Name each header from the board list: CPU_FAN, CPU_OPT, CHA_FAN1, CHA_FAN2, CHA_FAN3, H_AMP_FAN, W_PUMP, EXT_FAN.
3. Arm control, then use **Identify**. That header goes to 100% for about three seconds and then returns to its previous duty. Watch which fan moves.
4. Set the CPU header to critical. Put a pump preset on W_PUMP. Case fans can use Quiet or Balanced and the hottest-disk source.
5. Choose curve or manual, enable the header, and arm control. Monitor mode never writes.

A missing curve sensor drives that header to 100%. A spun-down disk source drops to the header’s minimum instead.

Curves slew by default at 20% per second and ignore cooling of less than 2 °C, so the fans do not hunt.

## Preview without the motherboard

```bash
docker compose up --build
```

`RAMPAGE_SIMULATE=true` publishes a fake NCT6791D and a fake Aura device. Leave that variable false on the server.
