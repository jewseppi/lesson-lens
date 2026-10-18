---
name: language-lesson-study-pack
description: >-
  Turn an exported or pasted language-tutoring chat or lesson transcript into a
  structured, source-faithful study pack: a lesson summary, key sentences,
  vocabulary, the teacher's corrections, and spaced-repetition review assets
  (flashcards, fill-in-the-blank, translation drills, and a quiz), emitted as
  validated JSON. Works for any target language and any native language through
  a small language profile, and handles romanization/reading systems like
  pinyin, romaji, or furigana — or none at all for languages that don't need
  one. Use this whenever someone wants to review a language lesson, convert a
  tutor or chat log (LINE, WhatsApp, iMessage, a Zoom/Otter transcript, or
  pasted text) into study notes or Anki cards, pull out the vocabulary and the
  corrections their teacher made, or build review exercises from a conversation
  with a teacher — even if they don't use the words "study pack". The defining
  promise is fidelity: nothing is invented, corrections are preserved verbatim,
  and every item points back to the line it came from.
---

# Language Lesson Study Pack

You are turning a raw language-lesson transcript into a study package the
learner can actually review from. The whole value of this skill is **fidelity**:
a learner trusts these notes to study for a test or a conversation, so an
invented word or a mis-transcribed correction is worse than a missing one. Favour
being correct and traceable over being comprehensive.

This skill is model-driven end to end. You read the transcript and do the
segmentation, language detection, and classification yourself — there is no
regex parser to run and no format converter to install. That is what lets one
skill handle Mandarin over LINE and Spanish over WhatsApp with the same
instructions.

## Inputs

1. **A transcript.** An exported chat file, a pasted conversation, or a meeting
   transcript. Any format — see `references/source-formats.md` for what's been
   verified and the quirks of each (WhatsApp's system banners, LINE's two export
   shapes, etc.). If you're handed a file path, read it; if the user pasted
   text, use that.
2. **A language profile.** Four facts that make the pack language-agnostic:
   `target_language`, `script`, `reading_system`, `reading_policy`,
   `native_language`. See `references/language-profile.md` for the fields and
   ready-made profiles (Mandarin, Japanese, Korean, Spanish).

If the profile isn't given, **infer a draft from the transcript and state your
assumption in one line before proceeding** (e.g. "Looks like a Mandarin lesson;
I'll use Traditional characters with numbered pinyin, translating to English —
say the word if you'd rather have Simplified or diacritic pinyin."). Don't block
on it; a wrong guess is cheap to correct and the learner would rather see a draft.

## Workflow

Work in these steps. Steps 2–4 are the generation; step 5 is the self-review
that catches the errors that erode trust; step 6 proves the output is well-formed.

### 1. Establish the profile

Lock down the five profile fields (given or inferred). Everything downstream —
which script to write, whether to attach a reading, what language to translate
into — follows from this. When `reading_system` is `none` (e.g. Spanish), you
omit readings entirely; don't invent a romanization a learner of that language
would never use.

### 2. Segment and classify the transcript

Read the whole transcript first. Then:

- **Identify speakers and roles** (teacher vs learner). Names vary; infer from
  who is correcting whom and who is asking. If genuinely unsure, mark the role
  uncertain rather than guessing.
- **Assign a stable ID to every source message** you'll cite — e.g. `msg-0001`
  in order, or reuse the export's own timestamps. These are what `source_refs`
  point back to, so they must be stable within a run.
- **Separate lesson content from logistics.** Scheduling, "running 5 min late",
  Zoom links, stickers, and media placeholders are not study material — exclude
  them. But English (or native-language) lines that *explain* a word or a grammar
  point ARE lesson content; don't discard a line just because it isn't in the
  target language.
- **Split into sessions** if the transcript spans multiple lessons (a large time
  gap usually marks a boundary). One study pack per session.

### 3. Build the core study pack

For each session, produce, following the fidelity rules below:

- **summary.overview** — 2–3 sentences on what the lesson covered.
- **summary.short_recap** — 1–2 sentences for a glance before the next lesson.
- **summary.usage_notes** (optional) — cultural/usage/grammar notes that came up.
- **key_sentences** — the sentences worth remembering. Each has `target`
  (target-language text in the required script), `reading` (per policy),
  `translation` (native language), and `source_refs`.
- **vocabulary** — new or reviewed words: `term`, `reading`, `translation`,
  `pos_or_type`, and an `example_target` + `example_translation`. Prefer an
  example that actually appeared in the lesson; if you must compose one, keep it
  within the lesson's vocabulary and flag it.
- **corrections** — the highest-value items. For each: `learner_original` and
  `teacher_correction` **verbatim**, a short `reason`, and `source_refs`. If the
  teacher corrected it, capture it; this is what the learner got wrong and most
  needs to re-see.

### 4. Build the review assets

From the core pack (not from new material), generate `review`:

- **flashcards** — front = target term/sentence, back = reading + translation.
- **fill_blank** — a key sentence with one word replaced by `____`, plus the
  answer and an optional hint.
- **translation_drills** — both directions. `source_lang: "target"` (read it in
  the target language, give the native meaning — easier) and
  `source_lang: "native"` (produce the target language — harder).
- **quiz** — multiple choice on meaning, usage, or a correction, each with 2–4
  options, exactly one correct, and a one-line explanation. Distractors must be
  plausible but unambiguously wrong.

Every review item needs a unique, stable `id` so a review app can track progress.

### 5. Self-review pass (don't skip this)

Before you emit anything, re-read your draft against the transcript as a
sceptical reviewer, because these are the mistakes that make a learner stop
trusting the pack:

- **Reading/romanization errors** — wrong tones or syllables. In Mandarin,
  `ma1` (媽) vs `ma3` (馬) is a different word. Check every reading against the
  target text.
- **Translation errors** — meanings that are wrong or lose the point in context.
- **Script errors** — Simplified where Traditional was required, or the wrong
  character entirely.
- **Fabrication** — anything in the pack that isn't in the transcript. Remove it
  or move it to `confidence_flags`.
- **Corrupted corrections** — a correction where either side was captured wrong.

Fix what you can; for anything you remain unsure of, add a `confidence_flags`
entry (`area`, `level`, `detail`) rather than presenting a guess as fact.

### 6. Validate and emit

Write the pack as a single JSON object conforming to `lesson-data.v2`
(`references/lesson-data.schema.json`). Then validate it:

```bash
python scripts/validate_lesson_data.py <output>.json
```

The validator checks structure *and* the fidelity rules that matter to a buyer:
every key sentence and correction cites a source, the reading policy was
honoured, quiz answers point at a real option, and IDs are unique. Fix any
errors and re-run until it reports `PASS`. If the host app or the user wants
human-readable output too, render Markdown and an Anki-style CSV from the same
JSON — the JSON is the source of truth.

## Fidelity rules (the core of the skill)

These are language-independent and non-negotiable — they're why someone would
pay for this over a one-line prompt:

1. **Never invent content.** Use only what's in the transcript. Uncertain about
   a meaning or segmentation? Give your best guess and record it in
   `confidence_flags` — don't present it as settled.
2. **Preserve corrections verbatim.** Capture the learner's original and the
   teacher's fix exactly as written.
3. **Respect the script.** Write the target language in the script the profile
   specifies, consistently.
4. **Follow the reading policy.** Attach a reading to target-language text when
   the policy says so; omit it entirely when `reading_system` is `none`.
5. **Translate everything** into the native language — every target-language
   item gets a translation.
6. **Exclude non-lesson content.** Scheduling, greetings, media placeholders,
   and call notifications are noise.
7. **Cite sources.** Every key sentence and correction references the message
   ID(s) it came from, so the learner can trace it back.

## Output format

Return the `lesson-data.v2` JSON object as the primary artifact (raw JSON, no
markdown code fences if the host consumes it programmatically). One object per
session; if a transcript held several lessons, produce several packs.

## Reference files

- `references/language-profile.md` — profile fields and ready-made profiles.
- `references/source-formats.md` — per-format parsing notes and the honest
  verified/experimental matrix. Read it before claiming a format works.
- `references/lesson-data.schema.json` — the output contract.
- `scripts/validate_lesson_data.py` — schema + fidelity validator.
