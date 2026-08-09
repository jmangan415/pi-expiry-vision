#!/usr/bin/env python3
"""Run the bundled 16-photo test set and score it against ground truth.

Use this to check a new machine works, and to find out how fast it is.

    python3 scripts/selftest.py               # all 16 photos
    python3 scripts/selftest.py --limit 3     # quick check first
    python3 scripts/selftest.py --platform mac

Prints a per-photo scorecard and a summary. Exits 0 only if every date is
correct. Writes selftest-results.json next to the repo root.

Expect roughly 5 min per photo on a Raspberry Pi 5, and considerably less on
Apple Silicon (untested - please report what you get).
"""
import argparse, importlib.util, json, os, sys, time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PHOTOS = os.path.join(ROOT, "research", "photos")
TRUTH = os.path.join(ROOT, "research", "ground-truth.json")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def preflight(pq):
    """Fail early and clearly rather than 10 minutes in."""
    problems = []
    if not os.path.exists(TRUTH):
        problems.append(f"missing ground truth: {TRUTH}")
    if not os.path.isdir(PHOTOS):
        problems.append(f"missing test photos: {PHOTOS}")
    try:
        import PIL  # noqa: F401
    except ImportError:
        problems.append("Pillow not installed:  pip install pillow")

    # With EXPIRY_API_BASE we talk to a server someone else is running (MLX,
    # LM Studio, Ollama, a remote box) - no GGUF or llama-server needed here.
    if os.environ.get("EXPIRY_API_BASE"):
        return problems

    for label, p in (("model", pq.MODEL_PATH), ("projector", pq.MMPROJ)):
        if not os.path.exists(p):
            problems.append(f"missing {label}: {p}\n"
                            "      download it per the README's Setup section,\n"
                            "      or set EXPIRY_API_BASE to an OpenAI-compatible server")
    from shutil import which
    if not (which(pq.BACKEND) or os.path.exists(pq.BACKEND)):
        problems.append(f"llama-server not found: {pq.BACKEND}\n"
                        "      build llama.cpp, set EXPIRY_BACKEND to its path,\n"
                        "      or set EXPIRY_API_BASE to an OpenAI-compatible server")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only test the first N photos")
    ap.add_argument("--platform", choices=("rpi", "mac"), help="override auto-detection")
    a = ap.parse_args()

    if a.platform:
        os.environ["EXPIRY_PLATFORM"] = a.platform

    pq = load_module("pq", os.path.join(HERE, "process_queue.py"))
    sys.path.insert(0, HERE)
    read_expiry = load_module("read_expiry", os.path.join(HERE, "read_expiry.py"))

    problems = preflight(pq)
    if problems:
        print("cannot run:\n  - " + "\n  - ".join(problems))
        return 2

    truth = json.load(open(TRUTH))
    names = sorted(truth)
    if a.limit:
        names = names[:a.limit]

    external = os.environ.get("EXPIRY_API_BASE")
    if external:
        print(f"endpoint:  {external}  (external server — llama.cpp not used)")
        print(f"model:     {read_expiry.MODEL}")
    else:
        print(f"platform:  {pq.PLATFORM}  (n_gpu_layers={pq.NGL}, "
              f"threads={pq.THREADS or 'auto'}, flash_attn={pq.FLASH_ATTN}, "
              f"mlock={pq.USE_MLOCK})")
        print(f"model:     {os.path.basename(pq.MODEL_PATH)}")
        print(f"projector: {os.path.basename(pq.MMPROJ)}")
    print(f"testing {len(names)} photo(s)\n")

    server = pq.Server()
    t_load = time.time()
    server.start()
    load_s = time.time() - t_load
    print(f"model loaded in {load_s:.0f}s\n")
    os.environ["EXPIRY_API_BASE"] = server.base

    results, correct = [], 0
    try:
        for i, fname in enumerate(names, 1):
            want_raw = truth[fname]["date"]
            want_iso = read_expiry.to_iso(want_raw)
            t0 = time.time()
            try:
                r = read_expiry.read_item([os.path.join(PHOTOS, fname)])
            except Exception as e:
                r = {"error": f"{type(e).__name__}: {e}"}
            secs = time.time() - t0
            ok = r.get("expiry_iso") == want_iso
            correct += ok
            results.append({"photo": fname, "seconds": round(secs), "ok": ok,
                            "got_item": r.get("item"), "got_raw": r.get("expiry_raw"),
                            "got_iso": r.get("expiry_iso"), "want_iso": want_iso,
                            "truth_item": truth[fname]["item"],
                            "note": truth[fname].get("note", ""), "error": r.get("error")})
            print(f"[{i:2}/{len(names)}] {'PASS' if ok else 'FAIL'}  {fname:28} "
                  f"{secs:5.0f}s  {str(r.get('expiry_iso')):12} (want {want_iso})"
                  f"  {(r.get('item') or r.get('error') or '')[:34]}")
    finally:
        server.stop()

    times = [r["seconds"] for r in results] or [0]
    summary = {"platform": "external" if external else pq.PLATFORM,
               "endpoint": external or "managed llama-server",
               "n_gpu_layers": None if external else pq.NGL,
               "threads": None if external else pq.THREADS,
               "flash_attn": None if external else pq.FLASH_ATTN,
               "model": read_expiry.MODEL if external else os.path.basename(pq.MODEL_PATH),
               "projector": None if external else os.path.basename(pq.MMPROJ),
               "load_seconds": round(load_s),
               "correct": correct, "total": len(results),
               "mean_seconds": round(sum(times) / len(times)),
               "min_seconds": min(times), "max_seconds": max(times),
               "run_date": date.today().isoformat(), "results": results}
    out = os.path.join(ROOT, "selftest-results.json")
    json.dump(summary, open(out, "w"), indent=2)

    print(f"\n{'='*64}")
    print(f"dates correct: {correct}/{len(results)}")
    print(f"per photo: mean {summary['mean_seconds']}s "
          f"(min {summary['min_seconds']}s, max {summary['max_seconds']}s)")
    print(f"reference: Raspberry Pi 5 gets 16/16 at ~310s per photo")
    print(f"written to {out}")
    if correct < len(results):
        print("\nfailures are worth reporting - include selftest-results.json")
    return 0 if correct == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
