#!/usr/bin/env python3
"""Parser tests — no model, no network, runs in a second.

    python3 scripts/test_parsing.py

Every case here comes from something that actually went wrong. Run this after
touching read_expiry.py.
"""
import importlib.util, os, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("read_expiry", os.path.join(HERE, "read_expiry.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)

TODAY = date(2026, 8, 9)

DATES = [
    # (printed on pack, expected ISO, why this case exists)
    ("15 AUG",              "2026-08-15", "no year printed - the common case"),
    ("11 Aug",              "2026-08-11", "mixed case"),
    ("02.10.26",            "2026-10-02", "UK day-first, 2-digit year"),
    ("04.08.2026",          "2026-08-04", "UK day-first, 4-digit year"),
    ("2 October 2026",      "2026-10-02", "month spelled out"),
    ("USE BY 16 AUG",       "2026-08-16", "label prefix"),
    ("BEST BEFORE: 09 AUG", "2026-08-09", "best-before prefix"),
    ("15 AUG 257MBA",       "2026-08-15", "alphanumeric batch code"),
    ("11 AUG 215",          "2026-08-11", "numeric batch code"),
    ("10 AUG 26 18 215",    "2026-08-10", "batch code that starts with a valid year"),
    ("10 AUG 06.26 18 215", "2026-08-10", "Mac run: '06.' was read as year 2006"),
    ("10 AUG H4F",          "2026-08-10", "letter code"),
    ("21/08/23",            "2023-08-21", "explicit past date is preserved, not rolled"),
    ("N/A",                 None,         "model declined - must not invent"),
    ("",                    None,         "empty"),
    (None,                  None,         "missing"),
]

JSON_REPLIES = [
    ('{"item": "A", "expiry": "11 AUG"}', "11 AUG", "bare one-liner (llama.cpp)"),
    ('text\n```json\n{\n  "item": "A",\n  "expiry": "11 AUG"\n}\n```', "11 AUG",
     "pretty-printed in a fence (MLX) - cost 4/16 on the first Mac run"),
    ('The answer is {"item": "A", "expiry": "11 AUG"} ok', "11 AUG", "prose either side"),
    ('{"item":"X","expiry":"01 JAN"}\n{"item":"A","expiry":"11 AUG"}', "11 AUG",
     "two objects - the last one wins"),
    ('{"item": "A {weird} name", "expiry": "11 AUG"}', "11 AUG", "brace inside a string"),
    ("I cannot see a date.", None, "no JSON at all"),
]


def main():
    failures = 0

    print("date normalisation")
    for raw, want, why in DATES:
        got = rx.to_iso(raw, TODAY)
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {str(raw)!r:24} -> {str(got):12} {why}")

    print("\nJSON extraction")
    for text, want, why in JSON_REPLIES:
        obj = rx.last_json_object(text)
        got = obj.get("expiry") if obj else None
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {str(got):12} {why}")

    print(f"\n{'all passed' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
