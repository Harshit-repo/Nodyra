"""Download Nodyra's redacted operational evidence as a private JSON file."""

from __future__ import annotations

import argparse
import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path

MAX_BUNDLE_BYTES = 10 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path, default=Path("nodyra-support-bundle.json"))
    parser.add_argument(
        "--token-env",
        default="NODYRA_API_TOKEN",
        help="Environment variable containing an owner API token (never printed).",
    )
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{args.base_url.rstrip('/')}/ops/evidence-bundle"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if content_length > MAX_BUNDLE_BYTES:
                raise RuntimeError("Evidence bundle exceeded the 10 MiB safety limit")
            payload = response.read(MAX_BUNDLE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Evidence request failed with HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Evidence request failed: {exc.reason}") from None
    if len(payload) > MAX_BUNDLE_BYTES:
        raise SystemExit("Evidence bundle exceeded the 10 MiB safety limit")
    document = json.loads(payload)
    if document.get("redaction", {}).get("secrets_included") is not False:
        raise SystemExit("Server did not attest that the bundle excludes secrets")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Wrote redacted evidence bundle to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
