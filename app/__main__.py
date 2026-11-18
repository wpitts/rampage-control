import logging
import os
import socket
from pathlib import Path

import uvicorn

from app.main import create_app

log = logging.getLogger("rampage")


def _detect_host_ip() -> str:
    """Best-effort primary IPv4 of this host. No traffic actually leaves."""
    override = os.environ.get("RAMPAGE_ADVERTISE_IP", "").strip()
    if override:
        return override
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("255.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def _prepare_storage() -> None:
    if os.environ.get("RAMPAGE_CONFIG", "").strip():
        path = Path(os.environ["RAMPAGE_CONFIG"])
        path.parent.mkdir(parents=True, exist_ok=True)
        return
    preferred = Path("/config/config.json")
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        probe = preferred.parent / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        fallback = Path.cwd() / "data" / "config.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        os.environ["RAMPAGE_CONFIG"] = str(fallback)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _prepare_storage()
    host = os.environ.get("RAMPAGE_HOST", "0.0.0.0")
    port = int(os.environ.get("RAMPAGE_PORT", "8787"))
    if host in {"0.0.0.0", "::"}:
        log.info("Rampage Control dashboard: http://%s:%s/", _detect_host_ip(), port)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
