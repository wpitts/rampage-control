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

The image is published to GitHub Container Registry at `ghcr.io/wpitts/rampage-control:latest`, so Unraid can pull it directly. Do not paste the Git URL into the Repository field; that is a clone address, not a Docker image.

The first push to `main` triggers `.github/workflows/docker-publish.yml`, which builds and pushes that image. New GHCR packages start private, so a direct pull returns `denied: ... manifests/latest`. Open [the package page](https://github.com/wpitts/rampage-control/pkgs/container/rampage-control) once and set **Package settings → Danger Zone → Change visibility → Public**, or add a GHCR username and token under Unraid's Docker registry logins. After that the pull works with no credentials.

1. Load the sensor modules on the host. Add the lines from `scripts/unraid-go.sh` to `/boot/config/go` so they persist across reboots, then run them once from the Unraid console:

   ```bash
   modprobe nct6775
   modprobe coretemp
   modprobe drivetemp
   modprobe i2c-dev
   modprobe i2c-i801
   ```

2. Install the template so it appears under **Add Container → Template**:

   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user
   wget -O /boot/config/plugins/dockerMan/templates-user/my-rampage-control.xml \
     https://raw.githubusercontent.com/wpitts/rampage-control/main/unraid/rampage-control.xml
   ```

3. In the Docker tab, click **Add Container**, pick **rampage-control** from the Template list, and apply. Unraid pulls `ghcr.io/wpitts/rampage-control:latest`.

   To add it by hand instead, set Repository to `ghcr.io/wpitts/rampage-control:latest`, set Network to **Host**, turn on **Privileged**, and map `/sys` (rw), `/dev` (rw), and `/mnt/user/appdata/rampage-control` to `/config` (rw).

4. Leave the container privileged and keep the `/sys`, `/dev`, and appdata mappings. Set `RAMPAGE_TOKEN` if the LAN is shared.
5. The dashboard comes up on the Unraid server's own IP at `http://<tower>:8787`. Click **Web UI** on the container, or check the container log, which prints the exact `http://<ip>:<port>/` on startup.

The template uses host networking, so the dashboard binds straight to the Unraid IP with no port mapping to manage. Port 8787 must be free on the host. To move it, change both the **Web UI port** and the **Dashboard bind port** (`RAMPAGE_PORT`) to the same value. If you prefer a mapped bridge port or a dedicated container IP, switch Network back to **Bridge** or **Custom** and publish 8787.

### Build it yourself (optional)

```bash
docker build -t ghcr.io/wpitts/rampage-control:latest .
```

Pushes to `main` build and publish the image automatically through `.github/workflows/docker-publish.yml`.

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
