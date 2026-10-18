# Source Formats

This skill parses transcripts with the model, not a regex parser, so in
principle it reads whatever you paste. But "the model will probably read it" is
not the same as "tested". The value of this skill is fidelity, so be honest —
with yourself and in anything user-facing — about which formats have actually
been exercised.

## Verified / experimental matrix

| Format | Status | Notes |
|---|---|---|
| **LINE** (mobile + desktop export) | **Verified** | The original lesson-lens pipeline was built and run against real LINE exports end to end. Two shapes exist: mobile is tab-separated (`HH:MM⇥Speaker⇥Message`) under a `Mon, YYYY-MM-DD` date header; desktop is space-separated (`HH:MM Speaker Message`) under a `YYYY.MM.DD Weekday` header. |
| **WhatsApp** (`_chat.txt` export) | **Smoke-tested** | Parses in dry-runs against a synthetic sample. Not yet validated against a large corpus of real exports. See quirks below. |
| **iMessage / pasted chat** | **Experimental** | The model adapts; validate the output against the schema before trusting it. |
| **Zoom / Otter / meeting transcript** | **Experimental** | Works in principle (speaker-labelled turns), untested. Expect weaker turn attribution. |

Keep this matrix current as formats get real test coverage. "Verified" should
mean a real export went through and a human checked the result — not that it
looked plausible once.

## WhatsApp quirks to handle

WhatsApp exports are messier than LINE and are where a naive parser breaks:

- **Two line shapes.** iOS: `[2024-09-24, 14:05:32] Name: message`. Android:
  `9/24/24, 2:05 PM - Name: message` or `24/09/2024, 14:05 - Name: message`.
- **Locale-dependent dates.** `MM/DD/YY` vs `DD/MM/YYYY`, 12h vs 24h. Don't
  assume month-first; infer from context when day > 12.
- **Invisible characters.** Exports often carry a U+200E left-to-right mark right
  after the `]` or before the message. Ignore it.
- **System lines** that are not messages: `Messages and calls are end-to-end
  encrypted...`, `You created group ...`, `Name changed their phone number`.
  Exclude them like logistics.
- **Media placeholders:** `<Media omitted>`, `image omitted`, `audio omitted`,
  `sticker omitted`. These are not lesson content; note that an image existed if
  it matters, but don't invent its contents.
- **Multi-line messages.** A message continues on following lines until the next
  line that starts with a timestamp — join them.

## Promoting a format from experimental to verified

One real, hand-checked export is enough to move a format up a row. The honest
path:

1. Get one genuine export of that format.
2. Run the skill on it.
3. Run `scripts/validate_lesson_data.py` on the output.
4. Have a human spot-check a few corrections and readings against the transcript.
5. Save it as a fixture under `examples/`, and update the matrix.

Until that's done, describe the format as experimental in any listing. Shipping
a "supports WhatsApp, iMessage, Zoom + 20 more" claim with no fixtures is exactly
what undermines a fidelity-first skill.
