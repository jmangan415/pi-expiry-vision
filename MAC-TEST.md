# Mac test via LM Studio

Hand this folder to Claude Code on the Mac and point it at this file.

**Everything here was developed and measured on a Raspberry Pi 5. It has never
run on a Mac.** The point is to find out what happens. Surprising results are
findings, not mistakes.

## What this is

A local vision pipeline that reads product names and expiry dates off photos of
food packaging. On a Pi 5 (CPU-only, llama.cpp, Gemma 4 E4B Q4_K_M) it scores
**16/16** on the bundled test set at **~310 seconds per photo**.

This Mac has Gemma 4 E4B in **MLX** via LM Studio. Same model family, different
runtime, and a GPU. The question is accuracy and speed.

## Setup — three steps

**1. In LM Studio:** load `gemma-4-E4B` (the MLX build), then start the local
server from the Developer tab. Note the port (default `1234`) and the model
identifier it lists.

**2. Install the one Python dependency:**

```bash
pip3 install pillow
```

Pillow is only used to rotate and downscale the photos before sending them.

**3. Run the test:**

```bash
# quick check first — 3 photos
EXPIRY_API_BASE=http://127.0.0.1:1234/v1 \
EXPIRY_MODEL=google/gemma-4-e4b \
python3 scripts/selftest.py --limit 3

# then all 16
EXPIRY_API_BASE=http://127.0.0.1:1234/v1 \
EXPIRY_MODEL=google/gemma-4-e4b \
python3 scripts/selftest.py
```

Set `EXPIRY_MODEL` to whatever identifier LM Studio reports — check with
`curl -s http://127.0.0.1:1234/v1/models`.

With `EXPIRY_API_BASE` set, the harness never touches llama.cpp or any GGUF. It
just sends OpenAI-format chat requests with a base64 image.

## What to report

- Mac model and chip (e.g. M2 Pro, 16GB)
- `dates correct: N/16`
- Mean seconds per photo
- Anything that failed, and what it returned instead
- The generated `selftest-results.json`

Pi reference: **16/16, ~310s per photo**.

## Watch for these

**An empty date with no error** means the reply was cut off before the model
finished reasoning. Gemma emits 200–400 reasoning tokens *before* any visible
output, so a low response-length cap returns an empty string rather than an
error — it looks exactly like broken vision but isn't. The harness asks for 800
tokens; if LM Studio is also capping responses, raise it in the model settings.
This one wasted hours on the Pi.

**Prose instead of JSON.** The parser takes the last line beginning with `{`. If
the model chats instead of finishing with the JSON object, that shows up as a
missing date.

**Quantisation differs.** MLX 4-bit is not GGUF Q4_K_M, so a small accuracy
difference either way is a genuine result, not a bug.

## The three hard photos

If accuracy is below 16/16, these are the likely culprits and the interesting
ones to report:

- **`fage-yogurt.jpg`** — the real date `02.10.26` is dot-matrix on a curved,
  reflective lid, *and* a different product in the background clearly shows
  "16 AUG". Reading the background date is the classic failure. It caught our Pi
  out once when a prompt was worded loosely.
- **`mixed-salad.jpg`** — photographed upside down.
- **`garlic-baguettes.jpg`** — rotated 90°, small print.

## Optional, if the basics work

Point LM Studio at the **GGUF** build of the same model instead of MLX and rerun.
Same Mac, same photos, two runtimes — that comparison isn't published anywhere.
