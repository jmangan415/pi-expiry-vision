#!/usr/bin/env python3
"""Queue photos for local (on-Pi) expiry scanning. Returns immediately.

The local model takes ~5 minutes per item, so nothing here waits for it.
Photos are copied into a job directory and picked up by process_queue.py.

    queue_photos.py --category fridge front.jpg date.jpg
        -> ONE item, two views (e.g. label on one, date stamp on the other)

    queue_photos.py --category fridge *.jpg --separate
        -> each photo is its own item

Prints one JSON line per job created.
"""
import argparse, json, os, shutil, sys, tempfile, uuid
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(os.path.dirname(HERE), "data", "queue")
STAGES = ("pending", "working", "done", "failed")


def ensure_queue():
    for s in STAGES:
        os.makedirs(os.path.join(QUEUE, s), exist_ok=True)
    os.makedirs(os.path.join(QUEUE, ".tmp"), exist_ok=True)


def add_job(photos, category, note):
    """Build the job in a temp dir, then rename into pending/.

    The rename is atomic, so the worker can never observe a half-written
    job - no locking needed on either side.
    """
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    job_id = f"job_{stamp}_{uuid.uuid4().hex[:4]}"
    tmp = tempfile.mkdtemp(dir=os.path.join(QUEUE, ".tmp"))

    names = []
    for i, src in enumerate(photos, 1):
        ext = os.path.splitext(src)[1].lower() or ".jpg"
        name = f"{i:02d}{ext}"
        shutil.copy2(src, os.path.join(tmp, name))
        names.append(name)

    json.dump({"job": job_id, "category": category, "note": note,
               "created_at": datetime.now().isoformat(timespec="seconds"),
               "photos": names, "source": [os.path.abspath(p) for p in photos]},
              open(os.path.join(tmp, "job.json"), "w"), indent=2)

    dest = os.path.join(QUEUE, "pending", job_id)
    os.rename(tmp, dest)
    return {"job": job_id, "photos": len(names), "category": category}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photos", nargs="+")
    ap.add_argument("--category", default="fridge",
                    help="fridge, freezer, cupboard, medicine, ... (default: fridge)")
    ap.add_argument("--separate", action="store_true",
                    help="each photo is its own item (default: all photos = one item)")
    ap.add_argument("--note", default="", help="free-text hint passed through to the record")
    a = ap.parse_args()

    missing = [p for p in a.photos if not os.path.isfile(p)]
    if missing:
        print(json.dumps({"error": "no such file", "photos": missing}), file=sys.stderr)
        return 2

    ensure_queue()
    groups = [[p] for p in a.photos] if a.separate else [a.photos]
    for g in groups:
        print(json.dumps(add_job(g, a.category, a.note)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
