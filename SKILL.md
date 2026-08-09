---
name: local-expiry-scan
description: "Log food expiry dates from photos using a local vision model instead of your own vision. Use when the user sends photos of items to track. Queues the photos for a background worker running Gemma 4 on the host; free, offline, ~5 min per item."
---

# Local expiry scanning

Reads product names and expiry dates off packaging photos using **Gemma 4 E4B
running on this machine** — no API, no cost, nothing leaves the network.

Accuracy matches a frontier model on real fridge photos (16/16 items and dates
on the bundled test set). The trade is speed: **~5 minutes per item**, so work is
queued and processed in the background, never while the user waits.

Paths below are relative to this repo. If you run in a container, only the
`data/queue/` directory needs to be shared with the host — you never talk to the
model yourself.

## When the user sends photos: queue them

**Do NOT read the photos yourself.** Queue them. Reading them defeats the
purpose — the local model is meant to do the work. You are the fallback, not
the first resort.

```bash
# two views of ONE item (e.g. label on one photo, date panel on another)
python3 scripts/queue_photos.py --category fridge a.jpg b.jpg

# several DIFFERENT items, one photo each
python3 scripts/queue_photos.py --category fridge *.jpg --separate
```

Categories are free-form — `fridge`, `freezer`, `cupboard`, `medicine`,
`bathroom`, `cleaning`. Use your judgement from what you see.

Each call returns one JSON line per job:
`{"job": "job_20260809T105721_e6c5", "photos": 2, "category": "fridge"}`.
Report the job IDs and the total job count back to the user.

Use your own vision only when:

- a job **failed or the queue stalled** (see below), or
- the user explicitly asks ("read it yourself", "I'm waiting", "just tell me
  now"). Five minutes is fine for unpacking a shop; it is not fine for one item
  they are holding.

If you are unsure whether they want to wait, queue it and say so.

## Confirm the grouping before queueing

**Always list how you intend to group the photos** — which are separate items,
which belong together, and the resulting job count — then wait for the go-ahead.

Grouping mistakes are expensive. Each job takes ~5 minutes, and a wrongly-split
pair writes a confidently wrong product name into the database rather than
failing loudly. Confirming costs seconds.

Expect photos in **two batches**: separate items first, then any multi-photo item
on its own. Messaging apps do not guarantee photo order within a message, so
never infer pairing from position alone. If a batch looks like it contains a
pair — one photo showing a label with no date, another showing a date panel with
no product name — say so and ask.

## Gotchas that matter

- **At least one photo per item must show the product name.** Given only a date
  panel, the model will confidently name the wrong product — it called a beetroot
  pack "Mixed Berries" from its date sticker alone. Dates alone are not enough.
- **A worker must be running on the host**: `process_queue.py --watch 60`.
  Nothing happens until it is. It cannot run in the container — the model and
  `llama-server` live on the host.
- **No date visible → the job fails and nothing is written.** That is deliberate:
  a missing date is better than an invented one. Ask for a clearer photo of the
  date panel.
- **Names are stored as printed** ("Fresh Beetroot", not "Waitrose Fresh
  Beetroot"). If the same item was previously logged under a different wording it
  will appear as a second row.
- **Same name + same category overwrites.** The worker reports this explicitly
  (`REPLACED existing date ... — check this`) because a misread can otherwise
  replace a correct date silently.

Queue layout: `data/queue/{pending,working,done,failed}/<job_id>/`, each holding
the photos, `job.json`, and once processed `result.json`.

## Checking results, and recovering

```bash
python3 scripts/check_queue.py          # exit 0 = fine, exit 1 = needs attention
python3 scripts/check_queue.py --json   # machine readable
```

Run this when the user asks how a scan went, and as part of any morning routine.
It reports what was scanned, what failed, and whether the queue has **stalled** —
meaning nothing has completed recently, not merely that jobs are waiting their
turn (jobs queue behind each other at ~5 min apiece, so waiting is normal).

**You are the fallback.** The photos stay in the job folder, so if the worker
died, the model crashed, or a date was illegible, do it yourself:

1. `check_queue.py` — prints the full path of every photo needing attention
2. Read those photos directly with your own vision
3. Log them normally (`store_items.py --items '<json>'`, or your own tracker)
4. `check_queue.py --resolve <job_id>` to close the job out

Do **not** silently re-read failed jobs as routine. A failure usually means the
date panel was not legible, which the user will want to know — they may prefer to
retake the photo. Ask, unless they have told you to just get on with it.

## Storage

Results are written by `scripts/store_items.py` into `data/items.json` as
`{name, expiry, category, scanned_at}`, with `expiry` normalised to
`YYYY-MM-DD`. To write into an existing tracker instead, point `EXPIRY_SINK` at
any script accepting `--items '<json array>'`.
