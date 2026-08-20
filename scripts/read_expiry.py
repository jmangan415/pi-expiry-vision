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
    EXPIRY_MAX_TOKENS default 1200  # MUST stay generous: Gemma emits
                                    # hundreds of reasoning tokens before any
                                    # visible output. Too low returns an
                                    # empty string, not an error. Simple
                                    # photos use ~450; a frame containing two
                                    # products pushed one run to exactly 800
                                    # and it returned nothing at all.
"""
import argparse, base64, io, json, os, re, sys, urllib.request
from datetime import date, timedelta

API_BASE = os.environ.get("EXPIRY_API_BASE", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("EXPIRY_MODEL", "google/gemma-4-e4b")
MAX_TOKENS = int(os.environ.get("EXPIRY_MAX_TOKENS", "1200"))

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
    # Do NOT mention "USE BY or BEST BEFORE" here. Naming the labels makes the
    # model treat them as required: shown a bare date stamp it reads the date
    # correctly, decides it is not captioned, and returns null. Anchoring to
    # "the SAME package the product name appears on" fixes the background-decoy
    # case without that side effect. Measured 3/3 vs 2/3 for the wording above.
    "\nIdentify the product, then read the expiry date printed on the SAME "
    "package that the product name appears on. Other products may be visible "
    "behind or beside it - their dates are irrelevant, however clearly you can "
    "read them. Copy the date exactly as it is printed - do not reformat it.\n"
    # Packs often print a packing date beside the use-by (eggs: "26 AUG" next
    # to "BEST BEFORE 02 SEP"). Transcribing both left the parser to guess,
    # and it took the wrong one.
    "If several dates are printed, give the use-by or best-before date, not a "
    "packing or display date. Ignore batch codes and times printed near it.\n"
    "If no expiry date is visible in any photo, use null for the date rather "
    "than guessing.\n"
    'Finish your reply with one JSON object on its own line: '
    '{"item": "...", "expiry": "..."}'
)

MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

# day, month (name or number), optional 2/4-digit year. The year must not be
# followed by more digits/letters so batch codes ("15 AUG 257MBA") don't match.
# The year must not be followed by more digits, letters, or a '.' - otherwise
# a batch code like "10 AUG 06.26 18 215" reads 06 as the year and gives 2006.
DATE_RE = re.compile(
    r"(\d{1,2})\s*[./-]?\s*([A-Z]{3,9}|\d{1,2})"
    r"(?:\s*[./-]?\s*(\d{4}|\d{2})(?![0-9A-Z.]))?")


def _from_match(m, today, ignore_year=False):
    """One regex match -> (date, year_was_explicit) or None."""
    day_s, mon_s, year_s = m.groups()
    if ignore_year:
        year_s = None
    day = int(day_s)
    month = MONTHS.get(mon_s[:3]) if mon_s[:1].isalpha() else int(mon_s)
    if not month or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    try:
        if year_s:
            y = int(year_s)
            return date(y + 2000 if y < 100 else y, month, day), True
        d = date(today.year, month, day)
        if d < today - timedelta(days=60):          # already well past: next year
            d = date(today.year + 1, month, day)
        return d, False
    except ValueError:                              # e.g. 31 Feb
        return None


# How far out of range a date may be before we stop believing it. Food being
# added to a tracker is current stock, so a date years in the past is a
# misread, not a genuinely ancient item.
_MAX_PAST = timedelta(days=400)
_MAX_FUTURE = timedelta(days=5 * 365)


def to_iso_detail(raw, today=None):
    """'11 AUG' -> ('2026-08-11', flags).  UK day-first.

    Two behaviours worth knowing, both from real misreads:

    * SEVERAL DATES -> take the LATEST. Packs print a packing or display date
      beside the expiry ("26 AUG | 02 SEP" on eggs); on food the expiry is
      always the later one. Taking the first gave the packing date.

    * IMPLAUSIBLE EXPLICIT YEAR -> drop it and infer. The model reads adjacent
      batch codes as years ("24 AUG" above "6 228 - 17:09" came back as
      "24 AUG 2023"). The day and month are on the pack; the year is the
      suspect part, so we prefer the year-less reading and flag it rather than
      discarding an otherwise good date.
    """
    flags = []
    if not raw:
        return None, flags
    today = today or date.today()

    # Batch codes produce spurious matches: "215" parses as 21 May, and it is
    # LATER than the real date, so a naive "take the latest" picks the junk.
    # A genuine printed date has an alphabetic month ("10 AUG") or explicit
    # separators ("02.10.26"); a bare digit run has neither. Strong matches
    # win outright; weak ones are only consulted if there are no strong ones.
    strong, weak = [], []
    for m in DATE_RE.finditer(str(raw).strip().upper()):
        text = m.group(0)
        is_strong = m.group(2)[:1].isalpha() or any(c in text for c in "./-")
        got = _from_match(m, today)
        if got and got[1] and not (today - _MAX_PAST <= got[0] <= today + _MAX_FUTURE):
            # Explicit year is implausible - retry the same characters without it.
            retry = _from_match(m, today, ignore_year=True)
            if retry:
                flags.append(f"ignored implausible year in {m.group(0)!r}")
                got = retry
            else:
                got = None
        if not got:
            continue
        d = got[0]
        if today - _MAX_PAST <= d <= today + _MAX_FUTURE:
            (strong if is_strong else weak).append(d)

    candidates = strong or weak
    if not candidates:
        return None, flags
    if len(candidates) > 1:
        flags.append(f"{len(candidates)} dates found, took the latest")
    return max(candidates).isoformat(), flags


def to_iso(raw, today=None):
    """Backwards-compatible wrapper: just the ISO date, or None."""
    return to_iso_detail(raw, today)[0]


def last_json_object(text):
    """Return the last complete JSON object in the reply, or None.

    Brace-matched rather than line-based: different runtimes format the same
    reply differently. llama.cpp tends to emit a one-liner, MLX often
    pretty-prints inside a ```json fence. A line-scan finds only a bare '{'
    there and silently fails, which cost 4 of 16 on the first Mac run.
    """
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            found = obj
    return found


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

    parsed = last_json_object(text)
    if not parsed:
        return {"photos": paths, "error": "no JSON in reply", "raw": text.strip()[-200:]}

    raw = parsed.get("expiry")
    iso, flags = to_iso_detail(raw)
    out = {"photos": [os.path.basename(p) for p in paths],
           "item": parsed.get("item"),
           "expiry_raw": raw,
           "expiry_iso": iso}
    # Surface any judgement the parser made, so an override is visible in the
    # worker log rather than silently applied.
    if flags:
        out["date_notes"] = flags
    return out


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
