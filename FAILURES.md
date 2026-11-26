# Failures

What broke, what I tried, what fixed it. Newest at the bottom. Each entry is something
that actually happened in this project.

### F1: Addis Assistant docs unreachable from the build environment
- **What broke:** the coding agent's sandbox could not resolve `addisassistant.com`
  (`getaddrinfo ENOTFOUND`). After the host was allowed, the docs moved out of reach on
  another host (`docs.addisassistant.com`).
- **Tried:** fetching the site, `/docs`, `/api`. Then I read the homepage links and found
  the official SDK on PyPI (`addisai`), which is reachable.
- **Fix:** downloaded `addisai==0.14.0` and read its source to get the real contract:
  `POST https://api.addisassistant.com/api/v1/voice/generations`, key in the `x-api-key`
  header, body `{text, language, voice_id, output_format, voice_settings}`, response with
  a signed `audio_url`, and 19 Amharic voices (`am-yohanes-calm`, `am-meron`, ...).
  `pipeline/providers.py` calls that endpoint directly with `requests`.
- **Lesson:** when the docs are blocked or out of date, the official SDK source is the
  most precise documentation there is.

### F2: Keys and the cloud coding agent
- **What broke:** the cloud agent that wrote much of this code could not be given the API
  keys (I chose not to store them in its environment).
- **Fix:** the design was already set up for it: keys live only in a local `.env`. The
  agent built and tested everything that does not need a key (schema, cache, consent gate,
  the whole FFmpeg assembly, with synthetic assets), and I run the model stages on my own
  machine. This is also what "a stranger clones and runs one command" needs.

### F3: loudnorm measurements came back empty
- **What broke:** the offline end-to-end test crashed in the loudness pass with
  `JSONDecodeError: Expecting value`.
- **Cause:** FFmpeg runs with `-loglevel error` everywhere. `loudnorm` prints its
  measurement JSON at *info* level, so there was nothing to parse.
- **Fix:** `ffmpeg(..., loglevel="info")` for the two loudnorm passes only. The test now
  checks that the final mix reports LUFS.
- **Lesson:** the offline test with fake assets caught this before any real quota was spent.

### F4: Consent gate stopped the first narration run (working as intended)
- **What happened:** the first `python -m pipeline narration` stopped with
  `no recorded consent for 'mother' with scope 'story'`.
- **Fix:** not a bug. The order is: ask my parents, record their answers in
  `consent.json` and ETHICS.md, then run. The gate was also moved to the start of the
  narration stage, so it now covers the whole story (it had checked only shots that
  *show* a character, while s01 already talks about both of them).

### F5: FFmpeg would not install on Windows with winget
- **What broke:** `winget install Gyan.FFmpeg` answered `No package found matching input
  criteria`, after first asking me to accept the `msstore` source agreement.
- **Fix:** two routes. `winget install --id Gyan.FFmpeg -e --source winget` names the
  right source. The faster route, with no admin rights, is `pip install imageio-ffmpeg`,
  which ships a static ffmpeg. `pipeline/common.py::FFMPEG()` now looks in `$FFMPEG`, then
  PATH, then imageio-ffmpeg. Since that build has no `ffprobe`, `probe_duration` falls back
  to decoding with ffmpeg. I checked that this build has the filters the edit needs
  (subtitles, loudnorm, sidechaincompress, zoompan) and rendered the full offline film with
  only that binary on PATH.

### F6: Newer ffmpeg broke the loudness parser
- **What broke:** with the imageio-ffmpeg build (a newer version), the loudnorm pass failed
  with `JSONDecodeError: Extra data`, because it prints more log lines after the JSON block.
- **Fix:** `_loudnorm_json` finds the block that contains `"input_i"` and decodes only
  that object.

### F7: Gemini free tier: timeout, then 503 "high demand"
- **What broke:** the smoke test's one-word Gemini call hung for 180 s (read timeout),
  then twice returned `503 UNAVAILABLE: This model is currently experiencing high demand`.
  200 s lost in a smoke test.
- **Fix:** the smoke test now makes one attempt with a 30 s timeout. Gemini is not on the
  critical path: narration is Addis AI, and the Gemini TTS fallback and back-translation
  are optional. A free tier is not an SLA, so nothing essential depends on it.

### F8: FLUX.1 schnell rejects a seed
- **What broke:** `HTTP 400 ... Additional or unevaluated properties '/seed' at '/' not allowed`.
  Cloudflare's schnell schema has no seed field.
- **Fix:** the schnell request sends only prompt and steps. **Consequence:** schnell
  fallback images cannot be reproduced by seed, while FLUX.2 klein (primary) takes a seed.
  The smoke test now checks the primary model, not the fallback.

### F9: Kaggle CLI failed silently in the smoke test
- **What broke:** `FAIL kaggle 15.0s` with no message, because the error went to stdout
  and the check printed only stderr.
- **Fix:** the check prints both streams.

### F10: Time. No GPU clips for this deadline
- **Situation:** with about two hours left, ten Wan 2.2 clips on a Kaggle T4 (minutes per
  clip with CPU offload, plus model download) would not fit reliably.
- **Decision:** `models.video.provider = "kenburns"`. Every shot is a slow, varied
  pan/zoom over its FLUX keyframe, which is the brief's documented fallback. The Wan path
  (`kaggle/gpu_jobs.py`, `kaggle-push`/`pull`, clip cache keys) stays in the code. Switching
  the provider back to `kaggle` queues exactly the missing clips.

### F11: Duplicated people and swapped clothes in the keyframes
- **What broke (audience: me, looking at the first keyframes):** s02 showed my mother
  *twice*, s03 my father twice. s04 had extra standing figures. In s09 the man wore the
  woman's clothes. s07 had two men and a badly drawn hand. The coffee scene did not look
  like a Muslim home.
- **Cause:** my reference sheet asked for "the same person shown twice side by side, front
  and three-quarter view". FLUX.2 klein takes reference images literally, so it copied both
  figures into the scene. With two sheets passed and nothing saying which was which, it
  mixed their clothes.
- **Fix:**
  1. The sheet is now *one* figure, full body, alone (`keyframes.sheet_prompt`). Changing
     it changes the sheet's cache key, so every keyframe that uses it is regenerated.
  2. Every prompt states the head count ("only one person in the whole picture",
     "exactly two people, one woman and one man").
  3. `shot_prompt` maps references by position ("image 1 shows the woman ..., image 2 shows
     the man ...; do not swap clothes") and appends the negative list (duplicate person,
     twins, extra fingers) as text, since FLUX has no negative-prompt field.
  4. s06/s07 now share one setting: a simple Ethiopian Muslim home (woven mat, Arabic
     calligraphy, prayer rug). She sits on a low stool pouring from a black clay jebena.
     He sits in the same room, one cup in one natural hand.
- **Lesson:** the "character sheet with several views" habit is meant for human artists
  and for LoRA training. For a reference-image model, one clean figure per reference
  works better.

### F12: Kaggle's new tokens
- **What broke:** `kaggle` smoke check failed. The key I had was a new-style `KGAT_...`
  token, which the Kaggle CLI 2.x reads from `KAGGLE_API_TOKEN` and checks against
  `api.kaggle.com`, not from `KAGGLE_USERNAME`/`KAGGLE_KEY`. My display name ("sumeya
  Abdulsemed") is also not my handle (`sumeyaabdulsemed`).
- **Fix:** `kaggle_jobs._user()` accepts either style and reads the handle from
  `kaggle config view`. After that, the ACE-Step job was pushed to a T4.

---

_Add new entries below as they happen: quota limits (Cloudflare daily allowance, Kaggle
weekly hours), Wan on T4 (OOM, black frames, time), ACE-Step install on Kaggle, Amharic
pronunciation errors, character drift._
