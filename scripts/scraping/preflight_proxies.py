"""
Pre-flight: confirm each proxy in the file is reachable and gives us the
expected egress IP. Run this BEFORE burning captcha budget on a real scrape.

Usage:
    ./.venv/bin/python scripts/preflight_proxies.py \\
        --proxy-file configs/runtime/proxy_ips.txt

Exits 0 if every proxy passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


def check_one(proxy_url: str, timeout: float = 10.0) -> tuple[bool, str]:
    parsed = urlparse(proxy_url)
    expected_ip = parsed.hostname or ""
    try:
        resp = requests.get(
            "https://ifconfig.me",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
        )
        observed = (resp.text or "").strip()
        if observed == expected_ip:
            return True, observed
        return False, f"expected={expected_ip} observed={observed!r}"
    except requests.RequestException as exc:
        return False, f"error={exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-file", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    urls = [
        line.strip()
        for line in Path(args.proxy_file).read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        print(f"no proxies in {args.proxy_file}", file=sys.stderr)
        return 1

    failures = 0
    for url in urls:
        ok, msg = check_one(url, timeout=args.timeout)
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {url} {msg}")
        if not ok:
            failures += 1

    print(f"\n{len(urls) - failures}/{len(urls)} proxies healthy")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
