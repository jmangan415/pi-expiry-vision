# pi-expiry-vision

Read food expiry dates off packaging photos using a **local** vision model on a
Raspberry Pi 5. No API, no cost, no photos leaving the house.

Built as a drop-in local-scanning path for a [NanoClaw](https://github.com/nanocoai/nanoclaw)
agent's expiry tracker, but the scripts work standalone.

## Does it actually work?

Yes. On 16 real fridge photos it got **16/16 products and 16/16 dates**, and
agreed with a frontier model on **every** item they both read.

| | Frontier model | Pi 5 (llama.cpp, GGUF) | M5 Pro Mac (LM Studio, MLX) |
|---|---|---|---|
| Dates correct | 13/13 | **16/16** | 15/16 |
| Time per item | ~2 sec | ~5 min | **~8 sec** |
| Generation | — | 2.5 tok/s | **55 tok/s** |
| Cost | per-image | **£0** | **£0** |
| Works offline | no | **yes** | **yes** |

The Mac is ~39× faster, which makes local scanning genuinely interactive rather
than a background job. Its single miss was the model **declining** to guess on an
unlabelled date stamp — it returned `null` rather than inventing one, which is
the behaviour we want.

The test set is included in [`research/photos/`](research/photos) with
[ground truth](research/ground-truth.json), so you can reproduce it. It is not a
soft set — it includes a pack photographed **upside down**, one **rotated 90°**,
dot-matrix dates on curved foil, and a yoghurt pot with a *different* product's
date clearly legible in the background. All read correctly.

The trade is speed, not accuracy — and how much speed depends entirely on your
hardware. On a Pi, five minutes an item makes this a background job, which is why
the whole design is a queue. On Apple Silicon it is fast enough to just wait for.
The queue costs nothing either way.

Before touching the parser, run `python3 scripts/test_parsing.py` — it takes a
second, needs no model, and every case in it comes from something that actually
broke.

## How it works

```
agent ──► queue_photos.py ──► data/queue/pending/job_xxx/{01.jpg, job.json}
                                          │
                            process_queue.py (host, has the model)
                                          │
                              store_items.py ──► data/items.json
```

The agent only ever writes job folders. The worker owns the model, drains the
queue, and releases its ~6GB of RAM the moment the queue is empty. If you run
the agent in a container, only the queue directory needs to be shared — the
container never talks to the model.

**Nothing is ever lost.** The photos stay in the job folder, so if the worker
dies or a date is illegible, the agent can read those photos itself and log them
normally. `check_queue.py` reports what needs attention and prints the paths.

## Requirements

- **~8GB RAM** free (the model is ~5.3GB, plus a ~1GB projector)
- **llama.cpp** — `llama-server` on your PATH ([build instructions](https://github.com/ggml-org/llama.cpp))
- **Python 3** with Pillow (`pip install pillow`)
- Any hardware llama.cpp runs on. A Pi 5 gives ~5 min/item; a modern laptop is
  far quicker.

## Setup

```bash
git clone <this repo> && cd pi-expiry-vision
mkdir -p models && cd models

# language model (~5.3GB)
curl -LO https://huggingface.co/lmstudio-community/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf

# vision projector — use the F16 one, NOT the BF16 default (see below)
curl -L -o mmproj-F16.gguf \
  https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf
```

Then check it works:

```bash
python3 scripts/selftest.py --limit 3    # quick
python3 scripts/selftest.py              # all 16, scored against ground truth
```

And start the worker:

```bash
python3 scripts/process_queue.py --watch 60
```

It idles with no model loaded until work appears.

### Platform profiles

Settings are selected automatically by platform, and every one can be overridden.

| | `rpi` | `mac` |
|---|---|---|
| status | **measured** | still **unmeasured** — see below |
| `--n-gpu-layers` | 0 (CPU only) | 99 (Metal offload) |
| `--threads` | 3 | llama.cpp default |
| `--flash-attn` | off | on |
| `--mlock` | on | off |
| per photo | ~310s | — |

Force one with `EXPIRY_PLATFORM=rpi|mac`, or override individually via
`EXPIRY_NGL`, `EXPIRY_THREADS`, `EXPIRY_FLASH_ATTN`, `EXPIRY_MLOCK`, `EXPIRY_CTX`.

**Careful with the Mac numbers above.** The 8s/photo result came through LM
Studio serving an **MLX** build, using `EXPIRY_API_BASE` — which bypasses these
profiles entirely. The llama.cpp `mac` profile itself has still never been run,
so those flag values remain a reasoned guess.

Pointing at any OpenAI-compatible server is often the easier route on a Mac:

```bash
EXPIRY_API_BASE=http://127.0.0.1:1234/v1 EXPIRY_MODEL=google/gemma-4-e4b \
  python3 scripts/selftest.py
```

### ⚠️ Use the F16 projector

Most repos ship a **BF16** projector by default. On any ARM CPU without the
ARMv8.6 `bf16` extension — which includes the Pi 5 — every weight gets widened
to FP32 and run in FP32 maths. Measured on a Pi 5:

| Projector | Image encode | Accuracy |
|---|---|---|
| BF16 (the default) | 356s | 16/16 |
| **F16** | **115s** | 16/16 |
| Q8_0 | 127s | 16/16 |

**3.1× faster for a different download.** Identical answers — F16 actually has
3 more mantissa bits than BF16, so it's the more precise format for values in
range. BF16 only exists because it's what training hardware emits.

Check your CPU with `grep -o 'bf16\|i8mm\|asimddp' /proc/cpuinfo | sort -u`.

## Usage

```bash
# one item, two photos (e.g. label on one, date panel on the other)
python3 scripts/queue_photos.py --category fridge front.jpg date.jpg

# many items, one photo each
python3 scripts/queue_photos.py --category fridge *.jpg --separate

# how's it going?
python3 scripts/check_queue.py
```

Results land in `data/items.json` as `{name, expiry, category, scanned_at}`,
with `expiry` normalised to `YYYY-MM-DD`.

**At least one photo per item must show the product name.** Given only a date
panel, the model will confidently name the wrong product — it called a beetroot
pack "Mixed Berries" from its date sticker alone.

### Plugging into an existing tracker

Point `EXPIRY_SINK` at any script that accepts `--items '<json array>'`:

```bash
EXPIRY_SINK=/path/to/your/scan_items.py python3 scripts/process_queue.py --watch 60
```

### Configuration

| Env var | Default |
|---|---|
| `EXPIRY_PLATFORM` | auto-detected (`rpi` / `mac`) |
| `EXPIRY_BACKEND` | `llama-server` |
| `EXPIRY_MODEL_PATH` | `models/gemma-4-E4B-it-Q4_K_M.gguf` |
| `EXPIRY_MMPROJ` | `models/mmproj-F16.gguf` |
| `EXPIRY_THREADS` `EXPIRY_NGL` `EXPIRY_FLASH_ATTN` `EXPIRY_MLOCK` `EXPIRY_CTX` | from the platform profile |
| `EXPIRY_SINK` | `scripts/store_items.py` |
| `EXPIRY_DB` | `data/items.json` |
| `EXPIRY_NOTIFY` | *(unset)* — command receiving a summary on stdin |
| `EXPIRY_API_BASE` | *(unset)* — use an existing server instead of managing one |
| `EXPIRY_TEMPLATE` | *(unset)* — uses the chat template inside the GGUF |

## Using it from a NanoClaw agent

Copy [`SKILL.md`](SKILL.md) into your skill directory and mount this repo so the
agent can reach `data/queue/`. The critical instruction, which the skill spells
out, is: **the agent must queue the photos, not read them itself.** Otherwise it
will helpfully analyse them in two seconds and the local model never runs.

## What we learned

The measurements behind the defaults, all on a Pi 5 (Cortex-A76, 4 cores,
CPU-only). Raw data in [`research/`](research).

**The empty-response trap.** Gemma 4 emits 200–400 *reasoning* tokens before any
visible output. With a low `max_tokens` you get `finish_reason: "length"` and an
**empty string** — which looks exactly like broken vision. It isn't. Keep
`max_tokens` ≥ 800. This cost hours to diagnose.

**Don't disable reasoning.** Turning thinking off is a genuine 2.4× speedup
(515 → 40 output tokens), but accuracy collapsed to 2/4 on hard cases — and it
didn't fail honestly, it *invented* a plausible date. Slow and right beats fast
and confidently wrong.

**Quantisation format is irrelevant; size isn't.** Q4_K_M and a QAT q4_0 build
performed identically (2.51 vs 2.42 tok/s). At ~5.2GB and 2.5 tok/s you're
moving ~13GB/s — the Pi's memory ceiling. The CPU is idle waiting on RAM, so
clever formats can't help. Useful rule of thumb:

> **tokens/sec ≈ 11 ÷ (GB of weights read per token)**

That also means MoE models are the one architecture that genuinely helps here —
they read fewer bytes per token without shrinking the model.

**More threads isn't better.** `--threads 4` on 4 cores sped up image encoding
(129s → 111s) but *slowed* generation (2.46 → 2.20 tok/s), for a net loss.
Generation is memory-bandwidth bound and the cores just contend. 3 is the
sweet spot, and it leaves you a usable machine.

**Transcribe with the model, normalise in Python.** The model returns the date
exactly as printed — `15 AUG 257MBA`, `USE BY 16 AUG`, `02.10.26` — and a ~20
line parser turns that into a real date. This is why an OCR slip on a batch code
(`257MBA` read as `25THBA`) changed nothing: the parser only takes the date.
Asking the model for ISO format instead invites day/month errors — `02.10.26` is
2 October in the UK and 10 February to a model thinking American.

**Prompt anchoring is load-bearing.** "read the expiry date printed on **that
pack**" vs "on **the packaging**" is the difference between reading the yoghurt
pot and reading the garlic bread behind it. The vaguer wording reintroduced a
failure the benchmark had already caught — which is the argument for keeping a
ground-truth test set around.

**Image size doesn't matter.** Gemma encodes any image to a fixed 256 tokens, so
a 4032px photo and a 768px one cost exactly the same. Downscale to ~1024px to
save transfer, and expect nothing further.

## Licence

MIT. The test photos are of ordinary supermarket packaging.
