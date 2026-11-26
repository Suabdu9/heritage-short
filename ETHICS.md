# Ethics: consent, licences, labelling

## Consent

The film is about two real people, my mother and my father. It shows a painted likeness of
each of them, tells their story, and quotes my father's words. The pipeline will not
generate anything of them until `consent.json` records their consent for that scope
(`likeness` for pictures, `story` for narration). The code check is in
`pipeline/common.py::require_consent`.

| Person | What they agreed to | How (in person / call / voice note / written) | Date | Evidence kept |
|---|---|---|---|---|
| My mother | Painted (non-photoreal) AI likeness; this story told in the film; film posted publicly with an AI label | Asked by me directly,in person | 2026-11-26 | Consent recorded in `consent.json`; message kept privately, not in the repo |
| My father | Same, plus his own interpretation quoted in the narration (s07, s09) | Asked by me directly, in person | 2026-11-26 | Same |

How I asked: I explained the challenge, told them the story would be narrated in Amharic
by an AI voice (not their voice and not mine), and that the pictures would be AI-painted
characters inspired by them, not photos. I asked whether they agreed to their story and my
father's words being used, and to the film being posted publicly with an AI label. Both
agreed before anything of them was generated. The narration text was checked and approved
by a native speaker before any line was voiced (`narration_review` in `storyboard.json`).
The first images got details of their appearance and faith wrong (see DESIGN.md, audience
test 1), and these were corrected before release.

No real voice is cloned. The narrator is an AI voice (Addis AI `am-yohanes-calm`), not
my voice or theirs.

**Privacy.** The prompts describe the characters only in general terms (age, clothing,
style). No photos of my parents, no names and no hometowns are sent to any model API,
because some free endpoints may train on inputs.

## Licences

| Component | Licence / terms | Notes |
|---|---|---|
| FLUX.2 [klein] 4B (Cloudflare Workers AI) | Apache 2.0 (the 9B variant is non-commercial; not used) | outputs usable |
| FLUX.1 [schnell] (fallback) | Apache 2.0 | |
| Wan 2.2 TI2V-5B | Apache 2.0 | |
| ACE-Step 1.5 | MIT; outputs usable commercially | trained on licensed, royalty-free and synthetic data |
| Addis AI TTS | provider terms of service (addisassistant.com/terms) | _TODO: confirm output use rights_ |
| Gemini API (text, TTS) | Google AI Studio / Gemini API terms | free-tier inputs may be used to improve Google products |
| FFmpeg | LGPL/GPL (local tool) | |
| This repository's code | MIT | |

## Labelling

- An **end card** reads "Made with AI" and lists the models, rendered into both exports.
- The MP4 `comment` metadata says the same.
- When posting, I switch on the platform's AI-content disclosure (TikTok "AI-generated"
  label, YouTube "altered or synthetic content").
- Provenance marks are left intact: no watermark removal and no metadata stripping.
- The style is described by its qualities (gouache, warm, grainy), and no living artist
  is named in any prompt.
