# Flying Toward Each Other · ወደ እርስ በርሳቸው መብረር

A 60–90 second heritage short, narrated in Amharic with English subtitles, and the small
code studio that makes it. Before they met, my mother and my father each dreamed for years
that they could fly. After they married, the dreams stopped. My father thinks he knows why.

This repository turns one file, [`storyboard.json`](storyboard.json), into the finished film:
reference sheets → keyframes → moving shots → score → narration → edit, in two aspect ratios.

## Run it

```bash
python -m pip install -r requirements.txt     # Python 3.10+
cp .env.example .env                          # then fill in your keys (see below)
python -m pipeline film                       # or: make film
```

Narration is locked until a native speaker approves the text (`narration_review.approved` in `storyboard.json`).

`film` validates the storyboard, then runs every stage. Each stage skips work whose inputs
have not changed, so on a clean clone (outputs are committed) the command re-assembles the
film from cache **without calling any model**. Change one line of `storyboard.json` and only
that shot is regenerated (see [ARCHITECTURE.md](ARCHITECTURE.md#caching)).

You also need **FFmpeg** on your PATH:
Windows `winget install Gyan.FFmpeg` · macOS `brew install ffmpeg` · Linux `apt install ffmpeg`.

Outputs: `outputs/final/heritage_16x9.mp4` and `outputs/final/heritage_9x16.mp4`.

### Every command

| Command | What it does | Model calls |
|---|---|---|
| `python -m pipeline validate` | JSON Schema + semantic checks of the storyboard | none |
| `python -m pipeline smoke` | one cheap call per provider, latency to `runs.jsonl` | 1 each |
| `python -m pipeline script` | word counts + Gemini back-translation of each Amharic line next to our subtitle | Gemini, per changed line |
| `python -m pipeline review` | prints every Amharic line with its English subtitle, for a native speaker to check | none |
| `python -m pipeline voices` | voices the first line with 6 Addis voices into `outputs/voices/` to choose one (`voices am-meron am-sara`, `--text "..."`) | Addis ×N |
| `python -m pipeline radio` | narration + temp score on a black screen (the first audience test) | TTS, per changed line |
| `python -m pipeline keyframes` | character reference sheets, then one keyframe per shot conditioned on them | FLUX, per changed shot |
| `python -m pipeline explore s05 4` | 4 seed variations of one keyframe, for choosing a seed | FLUX ×4 |
| `python -m pipeline shots` | uses Wan clips that exist; queues missing ones; Ken Burns fallback meanwhile | none (planner) |
| `python -m pipeline kaggle-push` / `kaggle-status` / `kaggle-pull` | runs the queued Wan 2.2 clips and ACE-Step score on a Kaggle T4, then downloads them | GPU |
| `python -m pipeline music` | chosen ACE-Step candidate, or a synthesised temp score | none |
| `python -m pipeline assemble` | timeline, ducking, loudness, subtitles, end card, both exports | none (FFmpeg) |
| `python -m pipeline film` | all of the above in order | only what changed |
| `python -m pipeline costs` | per-stage calls, latency, GPU minutes, list price from `runs.jsonl` | none |
| `python -m pytest -q` | unit tests + an offline end-to-end render with synthetic assets | none |

`--only s03,s07` limits new model calls to those shots. `--tts gemini` forces the Gemini
voice (used for the Addis vs Gemini comparison in [EXPERIMENTS.md](EXPERIMENTS.md)).

### Keys

| Variable | Where to get it | Used for |
|---|---|---|
| `ADDIS_API_KEY` | addisassistant.com → API keys | Amharic narration |
| `GEMINI_API_KEY` | Google AI Studio | script check, fallback TTS |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard → Workers AI → REST API | FLUX keyframes |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | kaggle.com → Settings → API (phone-verified account) | Wan 2.2 + ACE-Step on GPU |

Keys live only in `.env` (git-ignored) and are sent only in request headers; they are never
written to `runs.jsonl`.

## What it costs

Everything runs on free tiers; the measured numbers and their list-price equivalent are in
[COSTS.md](COSTS.md), regenerated from `runs.jsonl` by `python -m pipeline costs`.

## The pipeline in one picture

```
storyboard.json ──validate (schema/)──┬─► narration ──(durations)──────────────┐
                                      ├─► reference sheets ─► keyframes ─► shots ┼─► assemble ─► 16:9 + 9:16 MP4
                                      └─► music (ACE-Step on Kaggle / temp) ─────┘
                every call ─► runs.jsonl        consent.json gates real people
```

## Documents

- [DESIGN.md](DESIGN.md): audience, intent, why each shot exists, the two audience tests
- [ARCHITECTURE.md](ARCHITECTURE.md): stages, contracts, caching, failure handling
- [EXPERIMENTS.md](EXPERIMENTS.md): hypothesis → change → result
- [FAILURES.md](FAILURES.md): what broke and what fixed it
- [COSTS.md](COSTS.md): latency, GPU minutes, list price
- [ETHICS.md](ETHICS.md): consent, licences, labelling

## Licence

Code: MIT. Film and generated media: © the author, made with the models listed in
[ETHICS.md](ETHICS.md#licences), each under its own licence. The film is labelled as AI-made.
