"""Container health probe.

Exits 0 when the web GUI is disabled (there is nothing to probe) or when its
unauthenticated /healthz endpoint answers; exits 1 otherwise.
"""

import sys
from urllib.request import urlopen

from src.settings import AppSettings, load_settings

TIMEOUT_SECONDS = 5


def probe_url(settings: AppSettings) -> str:
    host = settings.gui_host
    if host in ("0.0.0.0", "::", ""):  # noqa: S104 - wildcard bind, probe loopback
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.gui_port}/healthz"


def check(settings: AppSettings) -> bool:
    if not settings.gui_enabled:
        return True
    try:
        with urlopen(probe_url(settings), timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status == 200
    except OSError:
        return False


def main() -> int:
    try:
        settings = load_settings(auto_migrate=False)
    except Exception:  # noqa: BLE001 - an unloadable config means an unhealthy app
        return 1
    return 0 if check(settings) else 1


if __name__ == "__main__":
    sys.exit(main())
