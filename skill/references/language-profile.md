# Language Profile

The profile is the small set of facts that lets one skill serve every language
instead of being hardcoded to one. It decouples the study-pack *structure* (the
same for everyone) from the language-specific *surface* (script, reading,
translation language).

## Fields

| Field | Meaning | Examples |
|---|---|---|
| `target_language` | The language being learned. | `Mandarin Chinese`, `Japanese`, `Korean`, `Spanish` |
| `script` | Orthography constraint, or `null` if the language has none. | `traditional`, `simplified`, `kanji+kana`, `hangul`, `null` |
| `reading_system` | Pronunciation aid to attach to target text, or `none`. | `pinyin-numeric`, `pinyin-diacritic`, `romaji`, `furigana`, `revised-romanization`, `none` |
| `reading_policy` | How often to attach a reading. `none` when there is no reading system. | `every_line`, `new_words_only`, `none` |
| `native_language` | The learner's language — used for translations and explanations. | `English` |

The output pack echoes the profile in `language_profile`, so a downstream app or
reviewer can see exactly what conventions a pack was built under.

## Why this replaces the old `language_mode`

The original project's schema had a `language_mode` with a `script` enum of
`traditional | simplified | both` and a field literally named `pinyin_policy`.
That bakes Chinese into the data model. The same idea generalizes cleanly once
the fields are named by *role*:

- `pinyin_policy` → `reading_policy` (+ a separate `reading_system` naming which
  system), so Japanese can ask for `romaji` and Spanish can ask for `none`.
- The field-level rename matters just as much: `zh`/`pinyin`/`en` became
  `target`/`reading`/`translation`. A key sentence is now
  `{ target, reading, translation }`, not `{ zh, pinyin, en }`.

## Ready-made profiles

### Mandarin Chinese

```yaml
target_language: "Mandarin Chinese"
script: "traditional"
reading_system: "pinyin-numeric"   # e.g. ni3 hao3
reading_policy: "every_line"
native_language: "English"
```

> Numbered-pinyin convention: mark the neutral/light tone with `5` (e.g. 的
> `de5`, 個 `ge5`, 嗎 `ma5`), so runs don't diverge between `5`, `0`, and no
> number — reading consistency is one of the things the skill is selling.

### Japanese

```yaml
target_language: "Japanese"
script: "kanji+kana"
reading_system: "romaji"           # or "furigana" if the app renders ruby text
reading_policy: "every_line"
native_language: "English"
```

### Korean

```yaml
target_language: "Korean"
script: "hangul"
reading_system: "revised-romanization"
reading_policy: "new_words_only"   # hangul is phonetic; learners quickly stop needing it
native_language: "English"
```

### Spanish (no reading system)

```yaml
target_language: "Spanish"
script: null
reading_system: "none"
reading_policy: "none"
native_language: "English"
```

When `reading_system` is `none`, leave `reading` empty or omit it. Don't invent a
romanization for a language that doesn't use one — a Spanish learner has no use
for a phonetic respelling of "descuento", and adding one signals the pack was
generated carelessly.
