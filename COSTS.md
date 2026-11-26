# Costs

Everything ran on free tiers: **$0 paid**. The table below is generated from `runs.jsonl`
by `python -m pipeline costs` and pasted here after each full run.

## Measured (from runs.jsonl)

### GPU: ACE-Step 1.5 on a Kaggle T4 (measured by the cloud agent's push/pull)

| stage | provider | calls | ok | errors | median latency (s) | total (s) | GPU min | list-price USD | model |
|---|---|---|---|---|---|---|---|---|---|
| music | kaggle (Tesla T4) | 3 | 3 | 0 | 8.3 | 25 | 0.4 | 0.0024 | acestep-v15-turbo + 0.6B LM planner |
| music | kaggle setup | 1 | 1 | 0 | 85.2 | 85 | 1.4 | 0.0083 | uv venv (Python 3.12) + ACE-Step install |
| music | kaggle (first attempt) | 1 | 0 | 1 | – | 15 | 0.3 | – | failed: Python 3.13 (FAILURES F13) |

About 2 GPU minutes in all for the score, out of a weekly free quota of about 30 hours.

### Local and hosted calls (narration, keyframes, assembly), from my laptop's runs.jsonl

| stage | provider | calls | ok | errors | cache hits | median latency (s) | total latency (s) | GPU min | list-price USD | models |
|---|---|---|---|---|---|---|---|---|---|---|
| assemble | ffmpeg | 62 | 62 | 0 | 0 | 5.1 | 758 | 0.0 | 0.0000 |  |
| keyframes | cloudflare | 10 | 10 | 0 | 10 | 14.1 | 130 | 0.0 | 0.0110 | @cf/black-forest-labs/flux-2-klein-4b |
| music | ffmpeg | 1 | 1 | 0 | 0 | 0.5 | 0 | 0.0 | 0.0000 |  |
| narration | addis | 9 | 9 | 0 | 9 | 6.1 | 57 | 0.0 | 0.0247 | addis/am-yohanes-calm |
| narration | ffmpeg | 9 | 9 | 0 | 0 | 0.1 | 1 | 0.0 | 0.0000 |  |
| reference | cloudflare | 2 | 2 | 0 | 2 | 10.8 | 18 | 0.0 | 0.0022 | @cf/black-forest-labs/flux-2-klein-4b |

Total list-price equivalent: **$0.038**; GPU time: **0.0 min**; actually paid: **$0** (free tiers). Generated from 118 lines of runs.jsonl.

### Wan 2.2 (not used for the final film)

0 GPU minutes. Skipped for time (FAILURES F10). Every shot uses a pan/zoom over its
keyframe. A T4 estimate for 10 clips at 480² is in EXPERIMENTS E6 (planned).

## Free-tier limits found (check when you start; they change)

| Service | Limit I found | Date checked |
|---|---|---|
| Cloudflare Workers AI | _TODO_ (the brief says roughly 50–170 images per day) | |
| Gemini API free tier | _TODO_ requests/min and per day for text and TTS | |
| Addis Assistant | _TODO_ free credits; `credits_remaining` is logged on every call | |
| Kaggle GPU | about 30 h/week; the session got 2× Tesla T4 (from the job's `nvidia-smi`) | 2026-11-26 |

## List-price assumptions (for "what would this cost if paid")

| Item | Assumption | Where it is set |
|---|---|---|
| Cloudflare FLUX image (1024², 4 steps) | $0.0011 per image | `PRICE_CF_IMAGE_USD` |
| Gemini TTS | $0.01 per 1,000 characters | `PRICE_GEMINI_TTS_1K` |
| Gemini text call (short) | $0.0005 per call | `PRICE_GEMINI_TEXT_CALL` |
| Addis TTS | credits used, as returned by the API (ETB) ÷ `ETB_PER_USD` | per call |
| GPU | $0.35 per T4-hour (GCP on-demand T4) | `PRICE_T4_HOUR` |

These are estimates. Update them with the current price pages before submitting, and note
the date.

## Assembly (local, measured in the offline test)

The offline end-to-end render (10 shots + end card, both aspect ratios, 1280×720 draft)
took about 70 s of CPU on a 4-core machine. Each FFmpeg render is a `provider: ffmpeg` line
in runs.jsonl with its latency.
