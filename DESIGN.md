# Design

> Written before generating anything. The audience-test sections are filled in from real
> conversations only. Each one says who, how, what they said (their words), and which
> lines of `storyboard.json` changed because of it.

## Brief

**Story.** Before they met, my mother and my father both had the same recurring dream:
they could fly. Each of them had it for years, without knowing the other did. Only after
they married did they find out. Then, after the wedding, both of them stopped having it.
My father's explanation: *maybe we were flying toward each other all along, and once we
met, we didn't need to fly anymore.*

**Logline.** Two people dream for years that they can fly, without knowing the other is
dreaming the same thing. When they finally meet, the dreams stop.

**Audience.** Our family and relatives. Most of them speak Amharic and will watch on a phone,
usually after the film is forwarded on WhatsApp or posted on TikTok. Younger cousins
abroad follow Amharic only partly and lean on the English subtitles.

**Where and how they watch.** Vertical, on a phone, often with the sound on low in a
room with other people. So: subtitles always on, the voice clearly above the music, the
picture readable at small size.

**Intent.** When it ends, the viewer should:
1. feel that my parents' meeting was meant to be,
2. smile at my father's explanation, and
3. want to tell a story from their own family.

**Define (how might we).** How might we tell a story that is half dream, half family
legend, in about a minute, so that relatives who know my parents recognise them, and
strangers still feel the coincidence?

## Choices that follow from the brief

| Choice | Because |
|---|---|
| Hand-painted gouache style, not photoreal | It is a dream story. A painted look also avoids putting realistic fake faces on real people (see ETHICS.md). |
| Narration in Amharic by a native TTS voice (Addis AI) | The family's language. A native model should get names and prosody right better than a general one; tested in EXPERIMENTS.md E1. |
| Narrator = me (the child) | It is a story told in the family about the family. |
| My father's words in quotes, near the end | His interpretation is the heart of the film. It needs his consent to be quoted (ETHICS.md). |
| Flight motif: night sky (open) → two figures far apart → two birds landing together → two birds flying side by side at sunrise (close) | One image that comes back and changes meaning, so the ending lands without explaining. |
| My parents are Ethiopian Muslims: my mother wears a hijab in every shot; the wedding is a nikah (white dress and headscarf; white thobe and kufi); my father's town shows a mosque minaret | So that relatives recognise *them*, and the AI does not default to the Orthodox Christian imagery it associates with "Ethiopian" (netela, gabi, church dome were in my first draft). |
| A native speaker approves every narration line before any of it is voiced (`narration_review` in the storyboard, enforced in code) | The words are my parents' story and my father's own words, and I cannot judge machine Amharic alone. |
| Coffee ceremony for the discovery | That is where Ethiopian families tell each other things. Relatives will recognise it at once. |
| Score: soft krar + strings + airy flute, sparse | Room for the voice. The flute is the "flying" theme and returns at the end. |
| Subjects centred, square keyframes | Both 16:9 and 9:16 come from the same image. Vertical is the main version. |

## Why each shot exists

(Also in `storyboard.json` as `reason`, validated by the schema.)

| Shot | Size | Reason |
|---|---|---|
| s01 | extreme wide | Establishing shot that sets the dreamlike night and the idea of flight before we meet anyone. |
| s02 | medium | Introduces my mother and her dream, so the audience meets her first as a dreamer. |
| s03 | medium | Mirrors s02 with my father, so the parallel between them is clear without explaining it. |
| s04 | extreme wide | Compresses the years they flew without knowing about each other; the distance between the two figures carries the beat. |
| s05 | medium | The turning point: they meet and marry, shown as one warm moment instead of a long courtship. |
| s06 | medium | Sets the discovery in a coffee ceremony, the place where Ethiopian families tell stories. |
| s07 | close | Close-up for the emotional surprise: my father realising he had the same dream. |
| s08 | wide | An empty sky shows the dreams have stopped; the absence is the strange part of the story. |
| s09 | medium | My father's explanation is the heart of the film, so it gets the longest shot and both of them together. |
| s10 | extreme wide | Closing image that returns to the opening sky, now at sunrise, to close the flight motif. |

## Audience test 1: the first picture cut (keyframes with narration and score)

**Prototype:** the first full render of `python -m pipeline film`: Amharic narration
(Addis AI, `am-yohanes-calm`, text approved by a native speaker first), temp score and
English subtitles, over the first FLUX keyframes with slow pan/zoom.

**Who:**

| # | Who (relation, age range, Amharic level) | How | Date |
|---|---|---|---|
| 1 | Me (the author, native Amharic speaker) | watched the render on my laptop | 2026-11-26 |
| 2 | Friend | in person | 2026-11-26 |
| 3 | relative | WhatsApp | 2026-11-26 |

**What we saw** (the problems the test found, shot by shot):

| Shot | Problem | Why it matters to this audience |
|---|---|---|
| s02 | My mother appears **twice** in one picture | Breaks the story: it is about one woman's dream |
| s03 | My father appears **twice** | Same |
| s04 | Extra figures; it should be exactly one woman and one man flying | The beat is "two people, far apart" |
| s06 | The coffee scene did not look like *our* home: she should sit and pour from a black clay jebena, in a Muslim home | Relatives must recognise my parents; my parents are Muslim, and the first draft used Orthodox imagery |
| s07 | Two men instead of one; his hand looked wrong; he should sit in the same room as s06 | Hands and duplicate people are what make viewers say "AI" and stop believing |
| s09 | The man wore the woman's clothes | My father in my mother's hijab and dress is wrong, and disrespectful |

What they said, in their words: [one or two quotes, e.g. "why is your mother there twice?"]

**Insights → changes** (all visible in `git log -p storyboard.json pipeline/keyframes.py`, details in FAILURES.md F11):

| Insight | Change | Where |
|---|---|---|
| The two-view reference sheet caused the duplicates | Sheet is now one figure, alone | `keyframes.sheet_prompt` |
| The model needs the head count stated | "only one person in the whole picture" / "exactly two people, one woman and one man" in every prompt with people | `shots[s02,s03,s04,s05,s06,s07,s09].image_prompt` |
| The model mixed up the two references | References are labelled by position ("image 1 is the woman, image 2 is the man; do not swap clothes"), plus a negative list (duplicate person, twins, extra fingers) | `keyframes.shot_prompt`, `style.negative` |
| The home must be recognisably Muslim and Ethiopian | s06 and s07 share one room: woven mat, Arabic calligraphy, prayer rug, black clay jebena; she sits on a low stool | `shots[s06,s07].image_prompt` |
| The reaction shot should match the coffee scene | s07 is now a medium close-up of my father sitting in the same room, one cup, one natural hand | `shots[s07].image_prompt`, `shot_size` |
| Characters must read as Muslim throughout | Mother: hijab in every shot; father: "no headscarf", thobe and kufi at the nikah; mosque minaret in s03 | `characters[].description`, `shots[s03,s05]` |

Result: after regenerating (cache: 2 sheets + 7 keyframes rebuilt, narration and the
empty-sky shots reused), I checked every regenerated keyframe: all six problems were fixed.

## Audience test 2: the near-final cut

**Prototype:** `outputs/final/heritage_9x16.mp4` with corrected keyframes, the chosen
ACE-Step score (candidate [0/1/2]) ducked under the narration, subtitles and end card,
sent on WhatsApp.

| # | Who (relation, age range, Amharic level) | How | Date |
|---|---|---|---|
| 1 | my mother | in person | 2026-11-26 |
| 2 | my father | | 2026-11-26 |
| 3 | relative | | 2026-11-26 |

**Questions:** Is this the story as you tell it? Did they look like the same two people
throughout? Did any Amharic word sound wrong? Did the music get in the way of the voice?
Would you forward it?

**What they said:** Yes ofcourse

**Changes:** none: confirmed the cut

## Storyboard history

`git log -p storyboard.json` shows every change. The commits made because of an audience
test say so in their message (`audience test 1: ...`).
