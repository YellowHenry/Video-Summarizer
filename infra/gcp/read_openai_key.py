from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    # Ensure repository root is importable when this script is called from anywhere.
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        from backend.config import get_openai_api_key
    except Exception:
        print("")
        return
    print(get_openai_api_key() or "")


if __name__ == "__main__":
    main()
