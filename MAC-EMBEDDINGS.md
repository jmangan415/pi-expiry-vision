# Fridge poetry — what does the image *look like* to the language model?

An experiment for Claude Code on the Mac. Curiosity, not production. Nothing in
this repo depends on it.

**Nothing here has been tried.** It is a sketch of an approach with the pitfalls
I can anticipate. If it turns out to be impossible, or the answer is boring, that
is a perfectly good result — say so rather than forcing it.

## The idea

A vision-language model doesn't "see" an image. The projector converts the photo
into **256 vectors**, and those vectors sit in the same space as word embeddings.
That's the whole trick — the language model reads them as if they were 256 words
in front of your prompt.

But they aren't words. Picture that space as a map where every token in the
vocabulary is a town. The projector drops 256 pins, and they land in open
countryside between towns. There's no word underneath any pin.

**Unless you look for the nearest town.** For each of the 256 vectors, find the
closest actual token in the vocabulary. That gives you 256 words: the model's
photo, rendered as the nearest thing to language.

Expect it to be *suggestive rather than legible*. Interpretability work that does
this typically gets fragments — `pink`, `##pack`, `label` — scattered through
noise. That's the honest expectation. Fragments would be a good result.

## Why on this Mac

llama.cpp never exposes the projector's output, so the Pi cannot do this at all.
MLX is a Python library where the model is an object you can poke at, and Gemma 4
E4B is already installed. 64GB is ample — the token embedding matrix is roughly
256k x 2560, about 2.7GB at fp32.

## Approach

```
photo ──► vision tower ──► 256 vectors ──► cosine similarity
                                                  │
                                    token embedding matrix (~256k x 2560)
                                                  │
                                          top-k tokens per position
```

Roughly:

1. Load the model through `mlx-vlm` (not LM Studio — you need the Python objects,
   not an HTTP endpoint)
2. Run the image through the vision tower / projector, stopping before the
   language model. Get the 256 x hidden_dim array
3. Pull the input token embedding matrix out of the language model
4. Normalise both, take the dot product, and read off the top 3–5 tokens for each
   of the 256 positions
5. Print them in a 16 x 16 grid — the patches map to image regions, so spatial
   structure may be visible

Two photos are worth doing:

- `research/photos/salmon-fillets.jpg` — clean, single subject. The sanity check:
  if `salmon` or `fish` doesn't appear anywhere, something is wrong with the setup
- `research/photos/fage-yogurt.jpg` — the interesting one. Its real date
  (`02.10.26`) is on the yoghurt lid, while a *different* product behind it shows
  `16 AUG`. Our Pi read the wrong date once. **Do tokens relating to the
  background packet show up strongly?** That's an independent check on whether
  the decoy is genuinely prominent in what the model perceives, or whether the
  failure was purely instruction-following

## The pitfall that will most likely bite

**Embedding scale.** Gemma multiplies token embeddings by `sqrt(hidden_dim)`
somewhere in the input path. If the projector's output is already scaled and the
token table isn't (or vice versa), nearest-neighbour returns pure noise.

If the output looks like uniform garbage, check this before concluding the idea
failed. Cosine similarity is scale-invariant *per vector*, so it should be robust
— but only if you are comparing against the same representation the model
actually consumes. Try both the raw table and the scaled version and see which
produces anything coherent.

Other things that could go wrong:

- **Wrong layer.** You want the projector's output — what gets handed to the
  language model — not the raw vision-encoder features from before the projection.
- **Tokeniser noise.** Top-1 is often a byte-fragment or unused token. Print the
  top 5 per position; the signal is usually there but not first.
- **Not 256.** If the image gets tiled (pan-and-scan) you may get a multiple of
  256. Fine, just note it.

## What to report

Write it up as `MAC-EMBEDDINGS-RESULTS.md` in this folder:

- Whether it worked at all, and what you had to do to get there
- The token grid for both photos (or a readable sample)
- Any recognisable words, especially for the salmon
- For the Fage: whether background-related tokens are prominent
- Whether scaling mattered, and which variant worked
- A code snippet that reproduces it, if you got something working

If it doesn't work, the write-up of *why* is just as useful — that tells us
whether this is worth another attempt.

## Scope

Timebox it. This is a curiosity, and a negative result is fine. Don't refactor
anything in the repo, don't touch the scripts, don't install anything heavyweight
beyond `mlx-vlm` and `numpy`/`mlx`.
