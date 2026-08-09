# Research data

Raw measurements behind the defaults in the main README. All on a Raspberry Pi 5
(Cortex-A76, 4 cores, 16GB, CPU-only, no GPU), Gemma 4 E4B Q4_K_M via llama.cpp.

## Files

| File | What it is |
|---|---|
| `photos/` | 16 real supermarket packaging photos, named by product |
| `ground-truth.json` | correct item + date for each photo, read by hand |
| `results16b.json` | the 16-photo accuracy run — **16/16 items, 16/16 dates** |
| `bench_projectors.txt` | BF16 vs F16 vs Q8_0 vision projector |
| `bench_quant.json` | Q4_K_M vs QAT q4_0 language model |
| `overnight.json` | threads 3 vs 4, and five ways of reducing reasoning tokens |

## Headline numbers

**Projector format** (`bench_projectors.txt`) — image encode time, 3 photos each:

| | Size | Encode | Accuracy |
|---|---|---|---|
| BF16 (the published default) | 992MB | 356s | 3/3 |
| **F16** | 990MB | **115s** | 3/3 |
| Q8_0 | 560MB | 127s | 3/3 |

3.1× for a different download. The A76 has no ARMv8.6 `bf16` extension, so BF16
weights are widened to FP32 and run in FP32. Q8_0 is smaller but *slower* — the
vision tower dequantises it back to float anyway, so you pay unpacking for
nothing.

**Language model quantisation** (`bench_quant.json`):

| | Generation | Wall | Accuracy |
|---|---|---|---|
| Q4_K_M | 2.51 tok/s | 308s | 3/3 |
| QAT q4_0 | 2.42 tok/s | 337s | 3/3 |

No difference. Both files are ~5.2GB, and at 2.5 tok/s that is ~13GB/s of memory
traffic — the Pi's ceiling. The CPU is idle waiting on RAM, so the format cannot
help. Hence: **tokens/sec ≈ 11 ÷ (GB read per token)**.

**Threads** (`overnight.json`, phase 1) — identical token counts both runs, so
this is like-for-like:

| | Encode | Generation | Wall |
|---|---|---|---|
| 3 threads | 129s | 2.46 tok/s | **335s** |
| 4 threads | **111s** | 2.20 tok/s | 342s |

The extra core speeds up the parallel encode but slows token generation, which is
bandwidth-bound and just contends. Net loss, and it costs you the machine.

**Reducing reasoning** (`overnight.json`, phase 2) — 4 photos per strategy:

| Strategy | Output tokens | Correct |
|---|---|---|
| baseline | 515 | **4/4** |
| `enable_thinking: false` | **40** | 2/4 |
| `reasoning_effort: low` | 468 | 4/4 *(silently ignored)* |
| "answer immediately" | 507 | 4/4 |
| "JSON only, no reasoning" | 415 | 4/4 |

Disabling thinking is a real 2.4× speedup and the only thing that meaningfully
cuts tokens — but accuracy halves, and it fails *dishonestly*: shown a pack it
could not read, it invented `21/08/23` rather than declining. Prompt wording
alone does essentially nothing; Gemma reasons regardless.

Note `reasoning_effort` is not supported for this model in llama.cpp — it was
accepted and ignored. Its apparent speedup in the raw data is a KV-cache artifact
(it reused the previous variant's identical prompt+image prefix, skipping ~128s
of encoding). Worth knowing if you benchmark prompt variants against a warm
server.

## Reproducing

```bash
python3 scripts/queue_photos.py --category fridge research/photos/*.jpg --separate
python3 scripts/process_queue.py
```

Then compare `data/items.json` against `ground-truth.json`. Expect ~80 minutes
on a Pi 5.

Note the dates are relative to when the photos were taken (August 2026). The
parser assumes a missing year means the nearest sensible one, so re-running this
much later will roll them forward a year.
