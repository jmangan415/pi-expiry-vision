#!/usr/bin/env python3
"""Report on the local-scan queue, and let the agent take over stuck work.

The photos live in the job folder, so nothing is ever lost. If the worker
died, llama-server crashed, or no date was visible, the agent can simply
read the photos itself and log them the normal way.

    check_queue.py                    # readable report
    check_queue.py --json             # machine readable
    check_queue.py --resolve <job>    # close out a job the agent handled

A job sitting in pending/ for a few minutes is normal - it is queued behind
others, each taking ~5 min. Only jobs older than --stuck-minutes are flagged.
"""
import argparse, glob, json, os, shutil, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(os.path.dirname(HERE), "data", "queue")
STAGES = ("pending", "working", "done", "failed")


def age_minutes(path):
    return (datetime.now().timestamp() - os.path.getmtime(path)) / 60


def load(job_dir):
    out = {"job": os.path.basename(job_dir), "dir": job_dir,
           "age_min": round(age_minutes(job_dir))}
    try:
        meta = json.load(open(os.path.join(job_dir, "job.json")))
        out["category"] = meta.get("category")
        out["photos"] = [os.path.join(job_dir, p) for p in meta.get("photos", [])]
    except Exception as e:
        out["error"] = f"unreadable job.json: {e}"
        out["photos"] = sorted(glob.glob(os.path.join(job_dir, "*.jpg")))
    res = os.path.join(job_dir, "result.json")
    if os.path.exists(res):
        try:
            r = json.load(open(res))
            out["item"] = r.get("item")
            out["expiry_iso"] = r.get("expiry_iso")
            out["error"] = r.get("error") or out.get("error")
        except Exception:
            pass
    return out


def survey(stuck_minutes):
    """Stuck means the queue has STALLED, not that a job is waiting its turn.

    Jobs take ~5 min each and run one at a time, so the last of 16 legitimately
    waits over an hour. What matters is whether anything has *completed*
    recently - if not, and work is outstanding, the worker has died.
    """
    counts, failed, waiting, recent = {}, [], [], []
    for s in STAGES:
        d = os.path.join(QUEUE, s)
        jobs = sorted(glob.glob(os.path.join(d, "job_*"))) if os.path.isdir(d) else []
        counts[s] = len(jobs)
        for j in jobs:
            info = load(j)
            info["stage"] = s
            if s == "failed":
                failed.append(info)
            elif s in ("pending", "working"):
                waiting.append(info)
            else:
                recent.append(info)
    recent.sort(key=lambda r: r["age_min"])

    # Minutes since the last completed job; if none ever completed, fall back
    # to how long the oldest outstanding job has been waiting.
    if recent:
        since_progress = min(r["age_min"] for r in recent)
    elif waiting:
        since_progress = max(w["age_min"] for w in waiting)
    else:
        since_progress = 0

    stalled = bool(waiting) and since_progress >= stuck_minutes
    return {"counts": counts, "failed": failed,
            "stuck": waiting if stalled else [],
            "waiting": len(waiting), "minutes_since_progress": round(since_progress),
            "stalled": stalled, "recent_done": recent[:10],
            "needs_attention": len(failed) + (len(waiting) if stalled else 0)}


def resolve(job):
    """Move a job the agent has handled itself into done/."""
    for s in ("failed", "pending", "working"):
        src = os.path.join(QUEUE, s, job)
        if os.path.isdir(src):
            dst = os.path.join(QUEUE, "done", job)
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.move(src, dst)
            json.dump({"resolved_by": "agent", "from": s,
                       "at": datetime.now().isoformat(timespec="seconds")},
                      open(os.path.join(dst, "resolved.json"), "w"), indent=2)
            return {"resolved": job, "was": s}
    return {"error": f"job not found in failed/pending/working: {job}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stuck-minutes", type=int, default=30)
    ap.add_argument("--resolve", metavar="JOB")
    a = ap.parse_args()

    if a.resolve:
        out = resolve(a.resolve)
        print(json.dumps(out))
        return 0 if "resolved" in out else 1

    s = survey(a.stuck_minutes)
    if a.json:
        print(json.dumps(s, indent=2))
        return 1 if s["needs_attention"] else 0

    c = s["counts"]
    print(f"queue: {c.get('pending',0)} pending, {c.get('working',0)} working, "
          f"{c.get('done',0)} done, {c.get('failed',0)} failed")
    if s["waiting"] and not s["stalled"]:
        eta = s["waiting"] * 5
        since = (f"last completion {s['minutes_since_progress']}min ago"
                 if s["counts"].get("done") else
                 f"nothing completed yet ({s['minutes_since_progress']}min waiting)")
        print(f"progressing normally — {since}; "
              f"~{eta}min left for {s['waiting']} outstanding job(s)")

    if s["recent_done"]:
        print("\nrecently scanned:")
        for r in s["recent_done"][:5]:
            print(f"  {r.get('item') or '?'} -> {r.get('expiry_iso') or '?'}")

    if not s["needs_attention"]:
        print("\nnothing needs attention.")
        return 0

    print(f"\n{s['needs_attention']} job(s) need attention — "
          "read these photos yourself, log them with scan_items.py, "
          "then run: check_queue.py --resolve <job>")
    LIMIT = 8
    for r in s["failed"][:LIMIT]:
        print(f"\n  FAILED {r['job']}  ({r.get('category')}, {r['age_min']}min ago)")
        print(f"    reason: {r.get('error') or 'unknown'}")
        for p in r.get("photos", []):
            print(f"    photo:  {p}")
    if len(s["failed"]) > LIMIT:
        print(f"\n  ... and {len(s['failed']) - LIMIT} more failed (use --json for all)")

    if s["stuck"]:
        print(f"\n  STALLED: nothing has completed for {s['minutes_since_progress']} min "
              f"with {len(s['stuck'])} job(s) outstanding — the worker is probably not "
              "running (process_queue.py on the host).")
        for r in s["stuck"][:LIMIT]:
            print(f"    {r['job']}  ({r.get('category')})")
            for p in r.get("photos", []):
                print(f"      photo: {p}")
        if len(s["stuck"]) > LIMIT:
            print(f"    ... and {len(s['stuck']) - LIMIT} more (use --json for all)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
