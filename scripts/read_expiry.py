#!/usr/bin/env python3
"""Read one food item's product name + expiry date from one or more photos.

One invocation == one item. Pass several photos when the information is
split across them (e.g. one showing the label, another the date stamp).

    read_expiry.py front.jpg date.jpg
    read_expiry.py *.jpg --separate     # each photo is its own item

Output: one JSON object per item on stdout.

Config (env):
    EXPIRY_API_BASE   default http://127.0.0.1:1234/v1
    EXPIRY_MODEL      default google/gemma-4-e4b
    EXPIRY_MAX_TOKENS default 800   # MUST stay generous: Gemma emits
                                    # ~200-400 reasoning tokens before any
                                    # visible output. Too low returns an
                                    # empty string, not an error.
"""
import argparse, base64, io, json, os, re, sys, urllib.request
from datetime import date, timedelta

API_BASE = os.environ.get("EXPIRY_API_BASE", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("EXPIRY_MODEL", "google/gemma-4-e4b")
MAX_TOKENS = int(os.environ.get("EXPIRY_MAX_TOKENS", "800"))

ONE_PHOTO = "The photo below shows one food item going into a kitchen expiry tracker."
MANY_PHOTOS = (
    "The {n} photos below are all of the SAME single food item, taken from "
    "different angles - for example one may show the product name and another "
    "the printed date. Treat them as one item and use whichever photo shows "
    "each piece of information."
)
TASK = (
    # "on that pack" matters. An earlier wording of "on the packaging" let the
    # model read a date off a different product in the background (it returned
    # the garlic bread's 16 AUG for a yoghurt pot dated 02.10.26).
    "\nIdentify the product, and read the expiry date printed on that pack "
    "itself (it may be labelled USE BY or BEST BEFORE). Other products may be "
    "visible behind or beside it - ignore their dates completely; only the date "
    "on the item in the foreground counts. Copy the date exactly as it is "
    "printed - do not reformat it.\n"
    "If no expiry date is visible in any photo, use null for the date rather "
    "than guessing.\n"
    'Finish your reply with one JSON object on its own line: '
    '{"item": "...", "expiry": "..."}'
)

MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

# day, month (name or number), optional 2/4-digit year. The year must not be
# followed by more digits/letters so batch codes ("15 AUG 257MBA") don't match.
DATE_RE = re.compile(
    r"(\d{1,2})\s*[./-]?\s*([A-Z]{3,9}|\d{1,2})"
    r"(?:\s*[./-]?\s*(\d{4}|\d{2})(?![0-9A-Z]))?")


def to_iso(raw, today=None):
    """'11 AUG' -> '2026-08-11'.  UK day-first; infers a missing year."""
    if not raw:
        return None
    today = today or date.today()
    m = DATE_RE.search(str(raw).strip().upper())
    if not m:
        return None
    day_s, mon_s, year_s = m.groups()
    day = int(day_s)
    month = MONTHS.get(mon_s[:3]) if mon_s[:1].isalpha() else int(mon_s)
    if not month or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    try:
        if year_s:
            y = int(year_s)
            return date(y + 2000 if y < 100 else y, month, day).isoformat()
        d = date(today.year, month, day)
        if d < today - timedelta(days=60):       # already well past: next year
            d = date(today.year + 1, month, day)
        return d.isoformat()
    except ValueError:
        return None


def encode(path, max_dim=1024):
    """EXIF-rotate and downscale. Gemma encodes any image to a fixed 256
    tokens, so going above ~1024px buys nothing and risks extra tiles."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def read_item(paths, timeout=1800):
    """paths: one or more photos of a SINGLE item -> dict."""
    intro = ONE_PHOTO if len(paths) == 1 else MANY_PHOTOS.format(n=len(paths))
    content = [{"type": "text", "text": intro + TASK}]
    for p in paths:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + encode(p)}})

    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": content}],
                       "max_tokens": MAX_TOKENS, "temperature": 0}).encode()
    req = urllib.request.Request(API_BASE.rstrip("/") + "/chat/completions",
                                 body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)

    choice = d["choices"][0]
    text = choice["message"].get("content") or ""
    if choice.get("finish_reason") == "length" and not text.strip():
        return {"photos": paths, "error": "truncated during reasoning - raise EXPIRY_MAX_TOKENS"}

    parsed = None
    for line in reversed(text.strip().splitlines()):
        line = line.strip().strip("`")
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                pass
    if not parsed:
        return {"photos": paths, "error": "no JSON in reply", "raw": text.strip()[-200:]}

    raw = parsed.get("expiry")
    return {"photos": [os.path.basename(p) for p in paths],
            "item": parsed.get("item"),
            "expiry_raw": raw,
            "expiry_iso": to_iso(raw)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photos", nargs="+")
    ap.add_argument("--separate", action="store_true",
                    help="treat each photo as its own item (default: all photos = one item)")
    a = ap.parse_args()

    groups = [[p] for p in a.photos] if a.separate else [a.photos]
    rc = 0
    for g in groups:
        try:
            out = read_item(g)
        except Exception as e:
            out = {"photos": [os.path.basename(p) for p in g],
                   "error": f"{type(e).__name__}: {e}"}
        if out.get("error") or not out.get("expiry_iso"):
            rc = 1
        print(json.dumps(out), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
