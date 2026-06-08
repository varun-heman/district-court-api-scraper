"""
Round-robin HTTP proxy pool.

Ported from ecourts-api-complete/scraper/delhi_hc_api/proxy_pool.py
(GCP tinyproxy fleet at asia-south1, bomhc-proxy-* VMs).

Reads a newline-delimited file of proxy URLs (e.g. `http://1.2.3.4:8888`).
File is read once at construction; regenerate via:

    gcloud compute instances list \\
      --filter="name~'^bomhc-proxy-' AND status=RUNNING" \\
      --format="value(networkInterfaces[0].accessConfigs[0].natIP)" | \\
      sed 's|^|http://|; s|$|:8888|' > /tmp/bomhc_proxy_ips.txt

Firewall rule `allow-tinyproxy-from-professeer` must include the source IP
of the machine running this scraper. See plans/20260420_gcp_proxy_current_state.md
on qa-prof for the gcloud setup notes.
"""

from __future__ import annotations

import logging
import os
import threading
from itertools import cycle
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class ProxyPoolEmptyError(RuntimeError):
    """Raised when the proxy file is missing, unreadable, or contains no usable URLs."""


class RoundRobinProxyPool:
    def __init__(self, path: str | Path):
        self._path = str(path)
        self._urls = self._load(self._path)
        self._cycle: Iterator[str] = cycle(self._urls)
        self._lock = threading.Lock()
        logger.info("[proxy] Loaded %d proxy URL(s) from %s", len(self._urls), self._path)

    @staticmethod
    def _load(path: str) -> list[str]:
        try:
            raw = Path(path).read_text()
        except FileNotFoundError as exc:
            raise ProxyPoolEmptyError(
                f"Proxy file not found: {path}. "
                f"Set ECOURTS_PROXY_FILE to a populated list, or run the scraper "
                f"with --no-proxy if you have direct egress whitelisted."
            ) from exc
        urls = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not urls:
            raise ProxyPoolEmptyError(f"Proxy file is empty: {path}.")
        return urls

    def next(self) -> str:
        with self._lock:
            return next(self._cycle)

    @property
    def size(self) -> int:
        return len(self._urls)

    @property
    def urls(self) -> list[str]:
        return list(self._urls)


def load_pool_from_env(env_var: str = "ECOURTS_PROXY_FILE") -> RoundRobinProxyPool | None:
    path = os.getenv(env_var, "").strip()
    if not path:
        return None
    return RoundRobinProxyPool(path)
