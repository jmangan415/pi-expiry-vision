#!/usr/bin/env python3
"""Drain the photo queue using the local vision model, write results to the DB.

Runs the model on this machine - no API, no cost. Roughly 5 minutes per item
on a Pi 5, so this is a background worker, never something a user waits on.

    process_queue.py            # drain once and exit (good for a timer)
    process_queue.py --watch 60 # poll forever, sleeping between passes

The llama-server is started only when there is work and stopped as soon as
the queue is empty, so an idle Pi keeps its ~6GB of RAM. Loading costs ~16s
and is paid once per drain, not once per item.

Config (env):
    EXPIRY_API_BASE   if set, use that endpoint and do NOT manage a server
    EXPIRY_MODEL_PATH  .gguf language model
    EXPIRY_MMPROJ      .gguf vision projector (use an F16 one on ARM)
    EXPIRY_THREADS     default 3
    EXPIRY_NOTIFY      optional command; receives a summary line on stdin
"""
import argparse, json, os, platform, shutil, signal, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUEUE = os.path.join(ROOT, "data", "queue")
VISION = os.environ.get("EXPIRY_VISION_DIR", HERE)

BACKEND = os.environ.get("EXPIRY_BACKEND", "llama-server")
MODEL_PATH = os.environ.get("EXPIRY_MODEL_PATH",
                            os.path.join(ROOT, "models", "gemma-4-E4B-it-Q4_K_M.gguf"))
MMPROJ = os.environ.get("EXPIRY_MMPROJ", os.path.join(ROOT, "models", "mmproj-F16.gguf"))
# By default use the chat template embedded in the GGUF (--jinja). Only pass an
# explicit template file if the user supplies one.
TEMPLATE = os.environ.get("EXPIRY_TEMPLATE", "")


# ---------------------------------------------------------------- platforms --
# Hardware differs enough that one set of flags is wrong somewhere. Override
# with EXPIRY_PLATFORM=rpi|mac, or set individual EXPIRY_* vars to win outright.
#
#   rpi  MEASURED on a Pi 5 (16GB, Cortex-A76, CPU-only). ~5 min per item.
#        3 threads beats 4: the 4th core speeds up image encoding but slows
#        token generation, which is memory-bandwidth bound. Net loss.
#
#   mac  UNTESTED. Apple Silicon has a Metal GPU, so the model is offloaded to
#        it rather than run on CPU - expect dramatically faster, likely well
#        under a minute per item. Thread count is left to llama.cpp, which
#        picks the performance cores. No --mlock: macOS restricts it without
#        privileges and the unified memory makes it pointless.

PROFILES = {
    "rpi": {"n_gpu_layers": "0", "threads": "3", "flash_attn": "off",
            "mlock": True, "ctx": "8192"},
    "mac": {"n_gpu_layers": "99", "threads": None, "flash_attn": "on",
            "mlock": False, "ctx": "8192"},
}


def detect_platform():
    if platform.system() == "Darwin":
        return "mac"
    return "rpi"


PLATFORM = os.environ.get("EXPIRY_PLATFORM") or detect_platform()
if PLATFORM not in PROFILES:
    sys.exit(f"unknown EXPIRY_PLATFORM {PLATFORM!r}; use one of {sorted(PROFILES)}")
PROFILE = PROFILES[PLATFORM]

THREADS = os.environ.get("EXPIRY_THREADS", PROFILE["threads"])
NGL = os.environ.get("EXPIRY_NGL", PROFILE["n_gpu_layers"])
FLASH_ATTN = os.environ.get("EXPIRY_FLASH_ATTN", PROFILE["flash_attn"])
CTX = os.environ.get("EXPIRY_CTX", PROFILE["ctx"])
USE_MLOCK = os.environ.get("EXPIRY_MLOCK", "1" if PROFILE["mlock"] else "0") == "1"
PORT = int(os.environ.get("EXPIRY_PORT", "37460"))
NOTIFY = os.environ.get("EXPIRY_NOTIFY", "")
SINK = os.environ.get("EXPIRY_SINK", os.path.join(HERE, "store_items.py"))

sys.path.insert(0, VISION)
import read_expiry  # noqa: E402  (same reader the benchmarks used)


def stage(name, job=""):
    return os.path.join(QUEUE, name, job)


def ensure_queue():
    for s in ("pending", "working", "done", "failed", ".tmp"):
        os.makedirs(stage(s), exist_ok=True)


def requeue_stale():
    """Anything in working/ is from a crashed run - put it back."""
    n = 0
    for job in sorted(os.listdir(stage("working"))):
        os.rename(stage("working", job), stage("pending", job))
        n += 1
    return n


def claim():
    """Atomically take the oldest pending job, or None."""
    for job in sorted(os.listdir(stage("pending"))):
        try:
            os.rename(stage("pending", job), stage("working", job))
            return job
        except OSError:
            continue          # another worker got it
    return None


class Server:
    """llama-server lifecycle. No-op if EXPIRY_API_BASE was supplied."""

    def __init__(self):
        self.external = bool(os.environ.get("EXPIRY_API_BASE"))
        self.proc = None
        self.base = os.environ.get("EXPIRY_API_BASE", f"http://127.0.0.1:{PORT}/v1")

    def start(self):
        if self.external or self.proc:
            return
        # Don't assume ensure_queue() has run - selftest.py starts a server
        # without ever touching the queue.
        log_path = os.path.join(ROOT, "data", "queue", "server.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log = open(log_path, "w")
        self.proc = subprocess.Popen([
            BACKEND, "--model", MODEL_PATH, "--mmproj", MMPROJ,
            "--host", "127.0.0.1", "--port", str(PORT),
            "--ctx-size", CTX, "--n-gpu-layers", NGL,
            "--batch-size", "2048", "--ubatch-size", "512",
            "--parallel", "1",
            "--cache-type-k", "f16", "--cache-type-v", "f16",
            "--flash-attn", FLASH_ATTN, "--no-webui", "--jinja",
        ] + (["--threads", THREADS] if THREADS else [])
          + (["--mlock"] if USE_MLOCK else [])
          + (["--chat-template-file", TEMPLATE] if TEMPLATE else []),
            stdout=log, stderr=subprocess.STDOUT)
        self._log = log
        for _ in range(900):
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server died on startup; see data/queue/server.log")
            try:
                with urllib.request.urlopen(self.base.replace("/v1", "/health"), timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(1)
        raise RuntimeError("llama-server never became healthy")

    def stop(self):
        if self.external or not self.proc:
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()
        self.proc = None


DB_PATH = os.environ.get("EXPIRY_DB", os.path.join(ROOT, "data", "items.json"))


def existing_row(name, category):
    """The row scan_items.py would overwrite, if any (it matches name+category)."""
    try:
        for row in json.load(open(DB_PATH)):
            if row.get("name", "").lower() == name.lower() and \
                    (row.get("category") or row.get("storage")) == category:
                return row
    except Exception:
        pass
    return None


def save_item(name, expiry_iso, category):
    """Hand off to scan_items.py so the DB stays single-sourced.

    The sink silently overwrites a row with the same name+category. That
    is usually right, but it means a misread can replace a correct date without
    trace - so report it when it happens and let the user judge.
    """
    prior = existing_row(name, category)
    payload = json.dumps([{"name": name, "expiry": expiry_iso, "category": category}])
    r = subprocess.run([sys.executable, SINK, "--items", payload],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(SINK)} failed: {r.stderr.strip()[:300]}")

    info = {"saved": r.stdout.strip()}
    if prior:
        info["overwrote_existing"] = True
        info["previous_expiry"] = prior.get("expiry")
        info["changed"] = prior.get("expiry") != expiry_iso
    return info


def process(job, server):
    d = stage("working", job)
    meta = json.load(open(os.path.join(d, "job.json")))
    photos = [os.path.join(d, p) for p in meta["photos"]]

    os.environ["EXPIRY_API_BASE"] = server.base
    result = read_expiry.read_item(photos)
    result["job"] = job
    result["category"] = meta.get("category")

    if result.get("error"):
        return result, "failed"
    if not result.get("item"):
        result["error"] = "no product identified"
        return result, "failed"
    if not result.get("expiry_iso"):
        # Model saw the pack but no date - a better photo is needed. Do NOT
        # write a guess into the DB.
        result["error"] = "no expiry date visible - reshoot the date panel"
        return result, "failed"

    result.update(save_item(result["item"], result["expiry_iso"], meta.get("category")))
    return result, "done"


def drain(server):
    done, failed = [], []
    started = False
    while True:
        job = claim()
        if job is None:
            break
        if not started:
            server.start()
            started = True
        t0 = time.time()
        try:
            result, outcome = process(job, server)
        except Exception as e:
            result, outcome = {"job": job, "error": f"{type(e).__name__}: {e}"}, "failed"
        result["seconds"] = round(time.time() - t0)

        json.dump(result, open(os.path.join(stage("working", job), "result.json"), "w"), indent=2)
        shutil.move(stage("working", job), stage(outcome, job))
        (done if outcome == "done" else failed).append(result)
        print(json.dumps(result), flush=True)
    if started:
        server.stop()
    return done, failed


def notify(done, failed):
    if not NOTIFY or not (done or failed):
        return
    lines = [f"Scanned {len(done)} item(s) locally" + (f", {len(failed)} need attention" if failed else "")]
    for d in done:
        line = f"  {d['item']} -> {d['expiry_iso']}"
        if d.get("changed"):
            line += f"  (REPLACED existing date {d['previous_expiry']} — check this)"
        elif d.get("overwrote_existing"):
            line += "  (updated existing row, same date)"
        lines.append(line)
    lines += [f"  FAILED {f.get('job')}: {f.get('error')}" for f in failed]
    try:
        subprocess.run(NOTIFY, shell=True, input="\n".join(lines),
                       text=True, timeout=60)
    except Exception as e:
        print(json.dumps({"notify_error": str(e)}), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="keep polling instead of exiting after one drain")
    a = ap.parse_args()

    ensure_queue()
    n = requeue_stale()
    if n:
        print(json.dumps({"requeued_after_crash": n}), flush=True)

    server = Server()
    while True:
        done, failed = drain(server)
        notify(done, failed)
        if not a.watch:
            print(json.dumps({"done": len(done), "failed": len(failed)}), flush=True)
            return 1 if failed else 0
        time.sleep(a.watch)


if __name__ == "__main__":
    sys.exit(main())
