from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.services.relation_consistency import find_relation_drifts, repair_relation_drifts


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit normalized relations against legacy JSON")
    parser.add_argument("--fix", action="store_true", help="Repair detected drift")
    parser.add_argument(
        "--source",
        choices=("relations", "json"),
        default="relations",
        help="Canonical side to use when repairing",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        before = find_relation_drifts(db)
        repaired = 0
        if args.fix and before:
            repaired = repair_relation_drifts(db, args.source)
            db.commit()
        after = find_relation_drifts(db)
        print(
            json.dumps(
                {
                    "source": args.source,
                    "fix": args.fix,
                    "detected": len(before),
                    "repaired": repaired,
                    "remaining": len(after),
                    "drifts": [item.as_dict() for item in after],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 1 if after else 0


if __name__ == "__main__":
    raise SystemExit(main())
