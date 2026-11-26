# Experiments

Format: **hypothesis → change → result → decision**. One variable changes at a time, and
the seed stays fixed unless the seed is the variable. Evidence is cited as `runs.jsonl`
lines (by `run_id`/`shot`) or files in `outputs/`.

Status: **planned** means the command is ready but I have not run it yet; **done**
means the result below is measured. No result is written before it is measured.

---

### E0: Voice choice (Addis AI audition)
- **Hypothesis:** among the 19 Amharic Addis voices, a calm male voice suits a story told
  at night in the family better than the default (`am-hamen`).
- **Change:** `python -m pipeline voices` voiced the s01 line with 6 voices (logged as
  `stage: voices` in runs.jsonl).
- **Result (done):** I listened to all of them and chose `am-yohanes-calm`. The narration
  text was approved by a native speaker before any line was voiced (`narration_review`).

### E1: Addis AI vs Gemini TTS for Amharic narration
- **Hypothesis:** a native Amharic TTS (Addis AI) pronounces names and the ejective
  consonants (ጥ, ቅ, ጭ) and handles sentence stress better than Gemini Flash TTS, at a
  similar latency.
- **Change:** same 9 lines, `python -m pipeline narration` (Addis) vs
  `python -m pipeline narration --tts gemini` (Gemini, voice Sulafat, style prompt "slow, warm").
- **Measure:** latency per line (runs.jsonl), seconds of speech per character, and a
  blind A/B by native speakers: which clip sounds like a person from our family?
- **Result:** _planned_
- **Decision:** _pending_

### E2: Does the style prompt help Gemini TTS?
- **Hypothesis:** a plain-language style instruction ("slowly and warmly, like a family
  story at night") makes Gemini's Amharic slower and warmer without hurting pronunciation.
- **Change:** `models.tts.style_prompt` empty vs set; provider gemini; same voice.
- **Measure:** speech duration per line (narration `current.json`), listener preference.
- **Result:** _planned_

### E3: Reference sheet conditioning vs text-only, for character consistency
- **Hypothesis:** passing the character sheet as a reference image to FLUX.2 klein keeps
  my mother recognisably the same across s02, s04, s05, s06 and s09 better than the fixed
  text description alone (FLUX.1 schnell, no references).
- **Change:** `models.image.model` = `flux-2-klein-4b` (sheets as refs) vs `flux-1-schnell`
  (text only); same prompts and seeds.
- **Measure:** 5 people shown the 5 keyframes of each set: "is this the same woman in all
  of them?" (yes/no per set); latency per image.
- **Result:** _planned_

### E4: Fixed description words vs re-described characters
- **Hypothesis:** identity drift comes mostly from describing the person differently in
  each prompt. `{mother}` placeholders expanded from one fixed description reduce it.
- **Change:** s02 and s06 prompts with `{mother}` vs a hand-written description of her in
  each prompt.
- **Result:** _planned_

### E5: Seed exploration for the hardest shot (s09)
- **Hypothesis:** 4 seeds give at least one keyframe where both faces and hands are clean.
- **Change:** `python -m pipeline explore s09 4`.
- **Result:** _planned_ (chosen seed goes into `shots[s09].seed`)

### E6: Wan 2.2 at 480×480 draft: GPU minutes per clip on a Kaggle T4
- **Hypothesis:** a 5 s (121-frame) 480² clip at 30 steps takes under 10 GPU minutes on a
  T4 with CPU offload, so all 10 shots fit in about 2 GPU hours.
- **Change:** `draft: true`, push all shots.
- **Measure:** `gpu_seconds` per clip in runs.jsonl (from kaggle-pull).
- **Result:** _planned_

### E7: fp16 vs bf16 for Wan on a T4
- **Hypothesis:** T4 has no fast bf16, so fp16 is about 2× faster, but fp16 may overflow
  into black frames.
- **Change:** `WAN_DTYPE` unset (fp16) vs `bf16` on one shot (s05).
- **Measure:** seconds per clip, `mean_pixel` / `black_frames` status.
- **Result:** _planned_

### E8: Wan clip vs Ken Burns: is the GPU worth it?
- **Hypothesis:** for close-ups (s07) a slow Ken Burns push-in is as good as a Wan clip;
  for motion shots (s02 rising, s09 birds) Wan is clearly better.
- **Change:** render both versions of s02, s07 and s09; blind preference test with 5 people.
- **Result:** _planned_

### E9: Three ACE-Step candidates: which score and why
- **Hypothesis:** with `[Instrumental]` and a sparse caption, at least one of 3 seeds
  stays out of the voice's way under ducking.
- **Change:** `models.music.candidates = 3`, seeds 4242–4244; listen to each at low
  volume under the narration in the rough cut (`models.music.chosen` = 0/1/2).
- **Result:** _planned_ (choice and reason recorded here)

### E10: Ducking settings
- **Hypothesis:** the starting values (threshold 0.02, ratio 6, attack 300 ms, release
  1500 ms) pump audibly between Amharic sentences; a longer release (2500 ms) sounds
  more natural.
- **Change:** `music.duck.release_ms` 1500 vs 2500, everything else fixed.
- **Measure:** listen on a phone speaker and on headphones; measured LUFS stays −14.
- **Result:** _planned_

### E11: Back-translation as a subtitle check
- **Hypothesis:** Gemini's blind back-translation of each Amharic line catches meaning
  drift between narration and the English subtitle.
- **Change:** `python -m pipeline script`; compare `back_translation` with `subtitle_en`.
- **Result:** _planned_

### E12: Loudness. Measured, not assumed
- **Hypothesis:** two-pass loudnorm lands within ±0.5 LU of −14 LUFS with true peak ≤ −1 dBTP.
- **Change:** none; read `loudness` from the `final` lines in runs.jsonl.
- **Result (offline test render with synthetic voice and temp score, 26 Nov):** output
  −14.0 LUFS, true peak −5.6 dBTP, `dynamic` normalisation. Within target. Re-check on the
  real mix.
