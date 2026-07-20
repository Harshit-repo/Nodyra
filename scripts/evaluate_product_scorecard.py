"""Validate scorecard evidence and prevent unsupported 10/10 claims."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DIMENSIONS = {
    "automated_gates",
    "operational_evidence",
    "discoverability",
    "failure_recovery",
    "user_outcomes",
}


def evaluate(scorecard: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    reviewed = date.fromisoformat(str(scorecard.get("reviewed_at")))
    cadence = int(scorecard.get("review_cadence_days", 31))
    age = (datetime.now(UTC).date() - reviewed).days
    if age > cadence + 14:
        errors.append(f"scorecard review is stale: {age} days")
    summaries: list[dict[str, Any]] = []
    categories = scorecard.get("categories")
    if not isinstance(categories, list) or not categories:
        return ["categories must be a non-empty list"], {}
    seen: set[str] = set()
    for category in categories:
        category_id = str(category.get("id") or "")
        if not category_id or category_id in seen:
            errors.append(f"missing or duplicate category id: {category_id!r}")
        seen.add(category_id)
        score = category.get("score")
        if not isinstance(score, int) or not 0 <= score <= 10:
            errors.append(f"{category_id}: score must be an integer from 0 to 10")
            score = 0
        if not category.get("owner"):
            errors.append(f"{category_id}: owner is required")
        dimensions = category.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != REQUIRED_DIMENSIONS:
            errors.append(f"{category_id}: all five score dimensions are required")
            dimensions = {}
        evidence = category.get("evidence")
        missing: list[str] = []
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{category_id}: evidence list is required")
        else:
            missing = [value for value in evidence if not (ROOT / value).exists()]
            if missing:
                errors.append(f"{category_id}: missing evidence: {', '.join(missing)}")
        if score == 10 and not all(dimensions.values()):
            errors.append(f"{category_id}: 10/10 requires every dimension to be evidenced")
        summaries.append(
            {
                "id": category_id,
                "score": score,
                "state": category.get("state"),
                "dimensions_green": sum(value is True for value in dimensions.values()),
                "missing_evidence": missing,
            }
        )
    return errors, {
        "schema_version": 1,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "review_age_days": age,
        "valid": not errors,
        "errors": errors,
        "categories": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scorecard", type=Path, default=ROOT / "product-scorecard.json")
    parser.add_argument("--output", type=Path, default=Path("product-scorecard-evidence.json"))
    args = parser.parse_args()
    document = json.loads(args.scorecard.read_text(encoding="utf-8"))
    errors, result = evaluate(document)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if errors:
        print("Product scorecard validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Product scorecard evidence contract is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
