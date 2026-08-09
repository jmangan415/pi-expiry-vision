#!/usr/bin/env python3
"""Minimal item store — the default sink for process_queue.py.

Keeps a JSON list at data/items.json. An item is identified by
(name, category): a repeat scan updates the existing row rather than
adding a duplicate.

    store_items.py --items '[{"name":"Baby Spinach","expiry":"2026-08-12","category":"fridge"}]'

If you already have an expiry tracker, point EXPIRY_SINK at your own
script instead — anything that accepts `--items '<json array>'` works.
"""
import argparse, json, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "items.json")


def load(path):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", required=True, help="JSON array of {name, expiry, category}")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()

    try:
        scanned = json.loads(a.items)
    except json.JSONDecodeError as e:
        print(f"bad --items JSON: {e}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(a.db), exist_ok=True)
    db = load(a.db)
    added = updated = 0

    for item in scanned:
        row = {"name": item["name"], "expiry": item.get("expiry"),
               "category": item.get("category", "fridge"),
               "scanned_at": datetime.now().isoformat()}
        for i, existing in enumerate(db):
            if existing.get("name", "").lower() == row["name"].lower() \
                    and existing.get("category") == row["category"]:
                db[i] = row
                updated += 1
                break
        else:
            db.append(row)
            added += 1
        print(f"  - {row['name']} [{row['category']}] expires: {row['expiry'] or 'unknown'}")

    json.dump(db, open(a.db, "w"), indent=2)
    print(f"\nDB updated: {added} added, {updated} updated -> {a.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
