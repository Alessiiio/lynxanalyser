#!/usr/bin/env python3
"""Print Keep-a-Changelog suggestions from wishlist items with status=done.

Usage:
  python3 scripts/wishlist_to_changelog.py
  python3 scripts/wishlist_to_changelog.py --mark-printed   # optional note only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wishlist_store import done_items_for_changelog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--mark-printed",
        action="store_true",
        help="Nur Hinweis ausgeben — Status bleibt 'done' (manuell in CHANGELOG übernehmen)",
    )
    args = ap.parse_args()

    items = done_items_for_changelog()
    if not items:
        print("Keine erledigten Wishlist-Einträge (status=done).", file=sys.stderr)
        return 1

    bugs = [i for i in items if i.get("type") == "bug"]
    feats = [i for i in items if i.get("type") == "feature"]

    print("## [Unreleased] — Vorschlag aus erledigter Wishlist\n")
    print("_Bitte manuell in CHANGELOG.md unter ### Added / ### Fixed einfügen, "
          "danach Status ggf. belassen oder Einträge archivieren._\n")

    if feats:
        print("### Added\n")
        for i in feats:
            line = (i.get("title") or "").strip()
            desc = (i.get("description") or "").strip()
            if desc:
                print(f"- {line} — {desc[:160]}")
            else:
                print(f"- {line}")
        print()

    if bugs:
        print("### Fixed\n")
        for i in bugs:
            line = (i.get("title") or "").strip()
            desc = (i.get("description") or "").strip()
            if desc:
                print(f"- {line} — {desc[:160]}")
            else:
                print(f"- {line}")
        print()

    print("<!-- IDs:", ", ".join(i.get("id", "")[:8] for i in items), "-->")
    if args.mark_printed:
        print("\n_Hinweis: --mark-printed gesetzt; Status unverändert (bewusst manuell)._")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
