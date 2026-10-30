"""Explicit URL replay. Read-only preflight unless --execute is supplied.

python -m scripts.targeted_live_test --database opportunities.db --url https://example.org/notice
No discovery, link traversal, source seeds, scheduling or automatic bid readiness.
"""

if __name__ == "__main__":
    # Also support python scripts/targeted_live_test.py from the repository root.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.controlled_live_test import main

    raise SystemExit(main(targeted=True))
