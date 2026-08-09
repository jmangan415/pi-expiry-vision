# Mac test — instructions for Claude

Hand this whole folder to Claude Code on a Mac and point it at this file.

**Everything here was developed and measured on a Raspberry Pi 5. None of it has
ever run on a Mac.** The `mac` profile is a reasoned guess, not a measurement.
The point of this exercise is to find out what actually happens and correct the
defaults. Treat surprising results as real findings, not as your mistakes.

---

## What this is

A local vision pipeline that reads product names and expiry dates off photos of
food packaging, using Gemma 4 E4B through llama.cpp. No API calls. On a Pi 5 it
scores 16/16 on the bundled test set at ~310 seconds per photo.

A Mac with Apple Silicon has a Metal GPU, so the model can be offloaded to it
rather than run on CPU. It should be **much** faster. How much is the question.

## Task

1. Get it running on this Mac
2. Run the self-test on all 16 photos
3. Report accuracy and timing, and suggest better `mac` profile defaults

## Setup

```bash
# 1. llama.cpp with Metal (the Homebrew build has it enabled)
brew install llama.cpp
llama-server --version

# 2. Python dependency
pip3 install pillow

# 3. Models — ~6.3GB total, into models/ in this folder
mkdir -p models && cd models
curl -LO https://huggingface.co/lmstudio-community/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf
curl -L -o mmproj-F16.gguf \
  https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf
cd ..
```

On the projector: **F16, not BF16.** On the Pi, BF16 was 3.1× slower because the
Cortex-A76 lacks the ARMv8.6 `bf16` extension. Apple Silicon M2 and later *do*
have native BF16, and with Metal offload this may not matter at all — that's one
of the things worth checking.

### Already have the model in MLX?

**MLX and llama.cpp are separate stacks.** MLX models are not GGUF, so
`llama-server` cannot load one, and llama.cpp's GGUF cannot be loaded by MLX.
You need either the GGUF above, or the MLX route below.

The MLX route is the more interesting test, because MLX is Apple's own framework
and may well beat llama.cpp on Apple Silicon. This harness never needs to own
the server — anything speaking the OpenAI chat-completions API will do:

```bash
# start whatever OpenAI-compatible server your MLX setup provides
# (check the mlx-vlm docs for the exact command - it changes between versions)

# then point the harness at it and it will not touch llama.cpp at all
EXPIRY_API_BASE=http://127.0.0.1:8080/v1 \
EXPIRY_MODEL=<model name the server expects> \
python3 scripts/selftest.py --limit 3
```

With `EXPIRY_API_BASE` set, the harness skips its GGUF and llama-server checks
entirely and just sends requests. The image is passed as a base64 `image_url`,
which is the standard OpenAI vision format — if the MLX server implements that,
this works unmodified.

**The comparison worth making, if you have the appetite:** the same 16 photos
through MLX and through llama.cpp on the same Mac. Same model, same photos, two
runtimes. Nobody publishes that number.

Caveats for any non-llama.cpp backend:

- **Timing is only comparable like-for-like.** MLX vs llama.cpp on the same Mac
  is fair. Either against the Pi is a different chip entirely.
- **Watch for an empty `expiry` with no error** — that means the reply was cut
  off before it finished reasoning. Raise `EXPIRY_MAX_TOKENS` (default 800).
- **Watch for prose instead of JSON.** The parser takes the last line starting
  with `{`. If a server strips or reformats the reply, that can break.
- Quantisation differs between stacks (MLX 4-bit is not GGUF Q4_K_M), so a small
  accuracy difference is a real finding rather than a bug.

## Run it

```bash
# quick check first — 3 photos
python3 scripts/selftest.py --limit 3

# then the full set
python3 scripts/selftest.py
```

It auto-detects macOS and applies the `mac` profile. It prints a per-photo
pass/fail with timings and writes `selftest-results.json`.

The `mac` profile currently guesses:

| Setting | Value | Reasoning (unverified) |
|---|---|---|
| `--n-gpu-layers` | 99 | full Metal offload |
| `--threads` | *(llama.cpp default)* | it picks performance cores |
| `--flash-attn` | on | supported on Metal |
| `--mlock` | off | macOS restricts it; unified memory makes it moot |
| `--ctx-size` | 8192 | plenty for one image plus a short prompt |

Override any of them: `EXPIRY_NGL`, `EXPIRY_THREADS`, `EXPIRY_FLASH_ATTN`,
`EXPIRY_MLOCK`, `EXPIRY_CTX`. Or force the Pi profile with
`--platform rpi` to compare.

## Things worth investigating

Only if the basics work — don't get distracted:

- **Does full GPU offload actually help?** Compare `EXPIRY_NGL=99` against
  `EXPIRY_NGL=0`. Report tokens/sec for each.
- **Does flash-attn on Metal help or hurt?** `EXPIRY_FLASH_ATTN=on` vs `off`.
- **Does the BF16 projector still lose on a Mac?** If M2 or later, native BF16
  support means it might be fine. The BF16 file is at
  `https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-BF16.gguf`.
- **How much of the time is image encoding vs generation?** The server log at
  `data/queue/server.log` reports `prompt eval time` and `eval time` separately.
  On the Pi it was ~115s encode and ~190s generation.

## What to report back

- Mac model and chip (e.g. M2 Pro, 16GB)
- Accuracy: `dates correct: N/16`
- Mean seconds per photo
- Whichever comparisons above you ran, with numbers
- Any settings that should change in the `mac` profile in
  `scripts/process_queue.py`
- `selftest-results.json`

## Known gotchas

- **`max_tokens` must stay ≥ 800.** Gemma emits 200–400 reasoning tokens before
  any visible output; a low limit returns an *empty string*, not an error, which
  looks exactly like broken vision. Already set correctly — don't "optimise" it.
- **Don't disable reasoning.** It's a real 2.4× speedup and it halves accuracy,
  and it fails by inventing plausible dates rather than declining.
- **Image size is irrelevant.** Gemma encodes any image to a fixed 256 tokens.
  Downscaling beyond ~1024px saves nothing.
- The test photos' dates are from August 2026. The parser assumes a missing year
  means the nearest sensible one, so ground truth still matches.

## If accuracy is below 16/16

That's interesting, not a failure — report which photos and what it returned.
The hard ones are `fage-yogurt.jpg` (a competing date is visible in the
background, and reading that instead is the classic failure), `mixed-salad.jpg`
(upside down), and `garlic-baguettes.jpg` (rotated 90°, small print).
