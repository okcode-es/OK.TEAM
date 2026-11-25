"""Offline OpenAPI contract exporter for okTeam backend."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
CONTRACT_PATH = ROOT_DIR / "contracts" / "openapi.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import create_app  # noqa: E402


def main() -> None:
    app = create_app()
    schema = app.openapi()
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Exported OpenAPI contract -> {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
