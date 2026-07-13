"""Wait for an HTTP readiness endpoint to return a 2xx response."""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=3.0)
    return parser.parse_args()


def wait_for_ready(url: str, timeout: float, interval: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=min(interval, 10.0)) as response:
                if 200 <= response.status < 300:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(interval)
    raise TimeoutError(f"{url} was not ready after {timeout:.0f}s ({last_error})")


def main() -> int:
    args = parse_args()
    try:
        wait_for_ready(args.url, args.timeout, args.interval)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
