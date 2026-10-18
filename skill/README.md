# Language Lesson Study Pack — skill

Turn an exported or pasted language-tutoring chat into a structured,
source-faithful study pack — lesson summary, key sentences, vocabulary, the
teacher's corrections, and spaced-repetition review assets (flashcards,
fill-in-the-blank, translation drills, quiz) — as validated JSON.

This skill is the portable, agent-ready core of **LessonLens**, an open-source
language-lesson study-pack project (a hosted app built with Flask + React +
SQLite, with chat sync, a web viewer, and spaced-repetition review). The skill is
just the reusable part — the prompts, the schema, and a validator — with nothing
to host and no database.

**Full project & source:** https://github.com/jewseppi/lesson-lens — if this
skill is useful, the complete app is there, and a star is appreciated.

## What makes it worth installing

- **Fidelity first.** Nothing is invented; teacher corrections are preserved
  verbatim; every key sentence and correction cites the transcript line it came
  from. A validator enforces this, not just good intentions.
- **Any language.** A small language profile (`target_language`, `script`,
  `reading_system`, `reading_policy`, `native_language`) replaces the original's
  hardcoded Traditional-Chinese-plus-pinyin assumption. Handles pinyin, romaji,
  furigana, Korean romanization — or none, for languages like Spanish.
- **Structured output.** A documented `lesson-data.v2` JSON contract, ready to
  render to Markdown, an Anki CSV, or a review UI.
- **Model-driven parsing.** No parser to install or configure; the agent reads
  whatever transcript you give it.

## Supported source formats — honest status

| Format | Status |
|---|---|
| LINE export (mobile + desktop) | **Verified** end to end |
| WhatsApp `_chat.txt` | **Smoke-tested** (synthetic sample) |
| iMessage / pasted chat | Experimental |
| Zoom / Otter / meeting transcript | Experimental |

"Experimental" means the model will attempt it and you should validate the
output — not that it's been proven. See `references/source-formats.md` for the
quirks of each and how a format graduates to "verified". We'd rather undersell
the format list than ship a fidelity-first skill on top of untested claims.

## Usage

Give an agent that has this skill a transcript and (optionally) a profile:

> "Here's my LINE export from today's Mandarin lesson — make me a study pack.
> Traditional characters, numbered pinyin."

The skill will segment the transcript, drop the logistics, build the pack,
self-review it, and validate it. If you don't state a profile it infers one and
tells you what it assumed.

## Layout

```
skill/
├── SKILL.md                         # instructions (the skill itself)
├── references/
│   ├── lesson-data.schema.json      # the output contract (lesson-data.v2)
│   ├── language-profile.md          # profile fields + ready-made profiles
│   └── source-formats.md            # per-format notes + verified/experimental matrix
├── scripts/
│   └── validate_lesson_data.py      # schema + fidelity validator
└── examples/
    ├── line-sample.txt              # Mandarin lesson (LINE format)
    ├── whatsapp-sample.txt          # Spanish lesson (WhatsApp format)
    ├── line-output.json             # dry-run result for the LINE sample
    └── whatsapp-output.json         # dry-run result for the WhatsApp sample
```

## License / provenance

The example transcripts are synthetic — no real student data.
