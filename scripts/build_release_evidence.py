"""Build a hash-addressed release evidence manifest from verified artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _evidence(path: Path) -> dict[str, str | int]:
    content = path.read_bytes()
    if not content:
        raise ValueError(f"evidence artifact is empty: {path}")
    try:
        relative = str(path.resolve().relative_to(ROOT))
    except ValueError:
        relative = path.name
    return {
        "path": relative.replace("\\", "/"),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _key_value(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=VALUE")
    key, item = value.split("=", 1)
    if not key or not item:
        raise argparse.ArgumentTypeError("expected non-empty NAME=VALUE")
    return key, item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage", choices=("internal", "pilot", "canary", "broad"), required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--image-digest", action="append", type=_key_value, default=[])
    parser.add_argument("--output", type=Path, default=Path("release-evidence.json"))
    args = parser.parse_args()

    expected = (ROOT / "VERSION").read_text().strip()
    if args.version != expected:
        raise SystemExit(f"release version {args.version!r} does not match VERSION {expected!r}")
    artifacts = [_evidence((ROOT / value).resolve()) for value in args.artifact]
    if not artifacts:
        raise SystemExit("at least one --artifact is required")
    image_digests = dict(args.image_digest)
    for name, digest in image_digests.items():
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise SystemExit(f"invalid image digest for {name}: {digest!r}")

    manifest = {
        "schema_version": 1,
        "product": "nodyra",
        "version": args.version,
        "source_sha": args.source_sha,
        "rollout_stage": args.stage,
        "generated_at": datetime.now(UTC).isoformat(),
        "documentation_version": args.version,
        "image_digests": image_digests,
        "artifacts": artifacts,
    }
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
