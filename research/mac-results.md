# Mac test results — Gemma 4 E4B via LM Studio (MLX)

Run of [`MAC-TEST.md`](MAC-TEST.md) on 2026-08-09. Pi reference: **16/16 at ~310 s per photo**.

## Environment

- **Host:** MacBook Pro (Mac17,8), **Apple M5 Pro**, 18 cores (6 Super + 12 Performance),
  **64 GB**, macOS Darwin 25.6.0
- **Runtime:** LM Studio server on `:1234` — `google/gemma-4-e4b`, arch `gemma4`,
  **MLX** 4-bit, vision-capable (VLM), 6.86 GB resident, **loaded in 5.6 s**
- Python 3.14.6, Pillow 12.2.0
- The model was *not* loaded at the start — `gemma-4-26b-a4b-qat` was. `gemma-4-e4b` was
  loaded explicitly; the 26b was left resident and idle (15.6 GB). It was not generating,
  so it should not have affected timings.

Terminology: **MLX** = Apple's array framework, LM Studio's native Apple-Silicon runtime.
**GGUF** = the llama.cpp file format the Pi used. **VLM** = vision-language model.

## Headline

| | Pi 5 (GGUF Q4_K_M) | This Mac (MLX 4-bit) |
|---|---|---|
| Dates correct, **harness as shipped** | 16/16 | **11/16** |
| Dates correct, **after 2 harness fixes** | 16/16 | **15/16** |
| Dates the *model* read correctly | 16/16 | **15/16** (+1 declined, no invention) |
| Mean seconds per photo | ~310 s | **8 s** (min 7, max 9) |

**~39x faster.** Roughly 440 completion tokens in ~8 s ≈ **55 tok/s**, against the Pi's
2.5 tok/s.

**4 of the 5 initial failures were the harness, not the model.** That is the main finding.

## The 5 failures

### 1–4. Multi-line JSON

`beef-burgers`, `feta-honey-filo-rolls`, `fresh-beetroot`, `garlic-baguettes`.

All four read their dates perfectly and all four scored zero. MLX sometimes pretty-prints
the object inside a fence:

```json
{
  "item": "Duchy Organic 4 British Beef Burgers",
  "expiry": "10 AUG 06.26 18 215"
}
```

The parser takes the last line *beginning* with `{`, so it sees a bare `{`, fails to decode
it, and reports "no JSON in reply". llama.cpp happened to emit one-liners; nothing in the
pipeline required that. Same prompt, same model family — purely a runtime formatting
difference.

### 5. `beef-burgers` again — a latent second bug

Even once the JSON parsed, `10 AUG 06.26 18 215` → **2006-08-10**. `DATE_RE`'s two-digit-year
group accepted `06` because the following `.` passed the negative lookahead.

The Pi never hit this: its read was `10 AUG 26 18 215`, where `26` is followed by a space and
lands on 2026 correctly. The README's claim that batch codes are harmless holds only for codes
containing letters (`257MBA`).

### 6. `thai-green-curry` — the one genuine miss

Not a vision failure. Its reasoning shows it *read the characters correctly*, the same ones
the Pi read:

> I see usage information ("10 AUG", "H4F", "NOT FOR EU"). I do *not* see a specific expiry
> date... The "10 AUG" seems to be a batch code... it is not clearly labeled as the consumer
> expiry date

It then returned `null` — declining rather than inventing, which is the designed behaviour.
Confirmed to be prompt sensitivity: swapping the "use null rather than guessing" line for one
saying an unlabelled date stamp still counts recovers `"10 AUG"` immediately.

**That prompt change was deliberately not applied.** The README documents that this wording is
load-bearing for the yoghurt decoy, and that a looser model happily invents plausible dates.
Trading a miss for a confident wrong answer is worse — that is a judgement call for the
maintainer.

## The three hard photos — all passed

- **`fage-yogurt`** — `02.10.26`, correct. **Ignored the 16 AUG decoy.** The classic failure
  did not reproduce.
- **`mixed-salad`** (upside down) — `11 Aug`, correct.
- **`garlic-baguettes`** (rotated 90°, small print) — `16 AUG`, correct; lost only to the
  JSON bug.

The photo that actually broke was a fourth one nobody had flagged.

## The empty-response trap did not fire

`finish_reason` was `stop` on all 16, and LM Studio did not cap responses. **MLX returns
reasoning in a separate `reasoning_content` field**, so `content` holds only the final JSON —
the specific failure mode that cost hours on the Pi cannot present the same way here.

It is not gone, though: reasoning tokens still count against `max_tokens`. Worst photo was
452 reasoning of 489 completion tokens against the 800 budget. **~90% of output is reasoning**;
visible output is only 25–40 tokens. Keep 800.

## Per-photo results (after fixes)

| # | Photo | Result | Secs | Got | Want |
|---|---|---|---|---|---|
| 1 | baby-spinach.jpg | PASS | 7 | 2026-08-12 | 2026-08-12 |
| 2 | beef-burgers.jpg | PASS | 8 | 2026-08-10 | 2026-08-10 |
| 3 | chicken-breast-fillets.jpg | PASS | 8 | 2026-08-12 | 2026-08-12 |
| 4 | diced-casserole-steak.jpg | PASS | 9 | 2026-08-10 | 2026-08-10 |
| 5 | fage-yogurt.jpg | PASS | 8 | 2026-10-02 | 2026-10-02 |
| 6 | feta-honey-filo-rolls.jpg | PASS | 8 | 2026-08-11 | 2026-08-11 |
| 7 | fresh-beetroot.jpg | PASS | 9 | 2026-08-15 | 2026-08-15 |
| 8 | garlic-baguettes.jpg | PASS | 8 | 2026-08-16 | 2026-08-16 |
| 9 | lamb-loin-chops.jpg | PASS | 7 | 2026-08-20 | 2026-08-20 |
| 10 | mixed-salad.jpg | PASS | 8 | 2026-08-11 | 2026-08-11 |
| 11 | pork-chipolatas.jpg | PASS | 8 | 2026-08-11 | 2026-08-11 |
| 12 | pork-meatballs.jpg | PASS | 7 | 2026-08-09 | 2026-08-09 |
| 13 | romaine-lettuce.jpg | PASS | 8 | 2026-08-09 | 2026-08-09 |
| 14 | salmon-fillets.jpg | PASS | 7 | 2026-08-11 | 2026-08-11 |
| 15 | spinach-feta-parcels.jpg | PASS | 8 | 2026-08-14 | 2026-08-14 |
| 16 | thai-green-curry.jpg | **FAIL** | 9 | `null` | 2026-08-10 |

Raw model output for every photo, exactly as returned, is in `raw16.json` (see Files).

## Changes made

Two minimal fixes in [`scripts/read_expiry.py`](scripts/read_expiry.py), **uncommitted** —
`git diff` shows them, `git checkout scripts/read_expiry.py` reverts:

1. **`last_json_object()`** — brace-matched extraction via `JSONDecoder.raw_decode`, replacing
   the line-based scan. Keeps last-object-wins, and handles fenced, pretty-printed,
   prose-prefixed and bare objects, including braces inside strings.
2. **`DATE_RE`** — added `.` to the year's negative lookahead.

Verified: the fixes attribute cleanly as **11 → 14 → 15**, and **all 16 date strings the Pi
produced parse identically** before and after, so the Pi's 16/16 is not at risk.

## Files

All three are in the repo root:

- [`selftest-results.json`](selftest-results.json) — the 15/16 fixed run
- [`selftest-results-AS-SHIPPED.json`](selftest-results-AS-SHIPPED.json) — the unmodified
  11/16 run, with a per-photo `model_read_date_correctly` flag
- [`raw16.json`](raw16.json) — raw `content` plus token counts for all 16

## Caveats on these numbers

- A higher-resolution probe on the failing photo was **a no-op** — that photo is natively
  768x1024, so `thumbnail()` never upscaled it and both requests sent an identical image.
  These results say nothing about resolution effects.
- LM Studio reported `prompt_tokens: 142` regardless of image size, which looks like image
  tokens are not counted in its MLX accounting. Low confidence, just an observation.

## The optional GGUF comparison is not runnable as-is

Only the MLX build of `gemma-4-e4b` is installed — the sole GGUF vision model present is
`qwen/qwen3.6-35b-a3b`, a different model. The same-Mac-two-runtimes comparison needs the
~5.3 GB GGUF download from the README's Setup section.

## Suggested follow-ups

- Commit the two parser fixes; they are portability fixes, not Mac-specific.
- Update the README platform table: the `mac` row can move from "untested" to measured, though
  note these numbers came via LM Studio/MLX, **not** the llama.cpp `mac` profile, which still
  has never been run.
- Add a multi-line-JSON reply and a `06.26`-style batch code to the parser's test cases.
