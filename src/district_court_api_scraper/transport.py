from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


APP_TOKEN_RE = re.compile(r'id=["\']app_token["\']\s+value=["\']([^"\']+)["\']', re.IGNORECASE)
APP_TOKEN_JSON_RE = re.compile(r'"app_token"\s*:\s*"([a-f0-9]{20,})"', re.IGNORECASE)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]
    json_data: dict[str, Any] | list[Any] | None


class ECourtsTransport:
    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        min_delay_seconds: float = 2.5,
        max_retries: int = 4,
        timeout_seconds: int = 30,
        proxy_url: str | None = None,
        proxy_pool: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url
        self.proxy_pool = proxy_pool
        self._last_request_at = 0.0
        self.app_token: str | None = None
        self._bootstrapped = False
        self._request_count = 0
        self._rotate_at = random.randint(150, 250)
        self.session = self._make_session(proxy_url)

    def _make_session(self, proxy_url: str | None) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": self.user_agent,
            "X-Requested-With": "XMLHttpRequest",
        })
        if proxy_url:
            # Pin one egress IP for this whole session — matches the worker-pinned
            # pattern used by ecourts-api-complete / dhc_phase2_runner. app_token
            # state is session-bound, so the proxy must stay sticky for the session.
            s.proxies = {"http": proxy_url, "https": proxy_url}
        return s

    def _rotate_session(self) -> None:
        """Replace the current session with a fresh one on a new proxy."""
        self.session.close()
        if self.proxy_pool is not None:
            self.proxy_url = self.proxy_pool.next()
        self.session = self._make_session(self.proxy_url)
        self.app_token = None
        self._bootstrapped = False
        self._request_count = 0
        self._rotate_at = random.randint(150, 250)
        logger.debug("[session] Rotated — next rotation in %d requests", self._rotate_at)

    @property
    def base_page(self) -> str:
        return f"{self.base_url}/"

    def bootstrap(self) -> bool:
        if self._bootstrapped:
            return True
        resp = self._request("GET", self.base_page, allow_json=False)
        if resp is None:
            return False
        token = _extract_app_token(resp.text)
        if token:
            self.app_token = token
        self._bootstrapped = True
        return True

    def refresh_token(self, text: str) -> None:
        token = _extract_app_token(text)
        if token:
            self.app_token = token

    def get_captcha_image(self) -> bytes | None:
        if not self.bootstrap():
            return None
        url = f"{self.base_url}/vendor/securimage/securimage_show.php?{int(time.time() * 1000)}"
        resp = self._request("GET", url, allow_json=False, headers={"Referer": self.base_page})
        if resp is None:
            return None
        if len(resp.content) < 100:
            return None
        return resp.content

    def post_route(self, route: str, data: dict[str, Any], *, referer_route: str = "casestatus/") -> HttpResponse | None:
        if not self.bootstrap():
            return None
        url = f"{self.base_url}/?p={route}"
        payload = {k: str(v) for k, v in data.items() if v is not None}
        payload["ajax_req"] = "true"
        if self.app_token:
            payload["app_token"] = self.app_token
        resp = self._request(
            "POST",
            url,
            data=payload,
            headers={"Referer": f"{self.base_url}/?p={referer_route}"},
        )
        if resp is not None:
            self.refresh_token(resp.text)
        return resp

    def get_absolute(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse | None:
        if not self.bootstrap():
            return None
        resolved = url if url.startswith("http") else urljoin(f"{self.base_url}/", url.lstrip("/"))
        resp = self._request("GET", resolved, headers=headers or {}, allow_json=False)
        if resp is not None:
            self.refresh_token(resp.text)
        return resp

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        allow_json: bool = True,
    ) -> HttpResponse | None:
        self._request_count += 1
        if self._request_count >= self._rotate_at:
            self._rotate_session()

        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    data=data,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 500:
                    self._sleep_backoff(attempt)
                    continue
                text = response.text
                json_data: dict[str, Any] | list[Any] | None = None
                if allow_json:
                    try:
                        json_data = json.loads(text)
                    except json.JSONDecodeError:
                        json_data = None
                return HttpResponse(
                    status_code=response.status_code,
                    text=text,
                    content=response.content,
                    headers={k: v for k, v in response.headers.items()},
                    json_data=json_data,
                )
            except requests.RequestException:
                self._sleep_backoff(attempt)
        return None

    def _throttle(self) -> None:
        if self.min_delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_delay_seconds:
            time.sleep(self.min_delay_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _sleep_backoff(self, attempt: int) -> None:
        base = min(10.0, 1.5 * attempt)
        time.sleep(base + random.random() * 0.2)


def _extract_app_token(text: str) -> str | None:
    for regex in (APP_TOKEN_RE, APP_TOKEN_JSON_RE):
        match = regex.search(text or "")
        if match:
            return match.group(1)
    return None
