# Secondary Study Assets Prompt — v1

You are generating supplementary study materials from a Mandarin Chinese lesson.
You will receive the structured `lesson-data.json` (or its key fields) and must
produce engaging, accurate review exercises.

## Rules

1. **Source fidelity.** All material must come from the provided lesson data.
   Do not introduce new vocabulary, sentences, or grammar not covered in the lesson.

2. **Traditional Chinese, pinyin on everything, English translations.**
   Same policy as the main summarizer.

3. **Mark uncertainty.** If generating a distractor option for a quiz or a
   fill-in-the-blank hint, and you are unsure it is appropriate, flag it in
   the output.

4. **Stable IDs.** Every item must have a unique, stable ID so the UI can
   track progress and interaction state.

## Required Outputs

### 1. Flashcards

For each vocabulary term and key sentence:

- **Front**: Chinese term or sentence
- **Back**: Pinyin + English translation
- **Hint** (optional): first character or category

### 2. Fill-in-the-Blank

Take key sentences and remove one word. Provide:

- Sentence with `____` replacing the target word
- The correct answer
- A hint if useful

### 3. Translation Drills

Provide bidirectional translation exercises:

- English → Chinese (harder)
- Chinese → English (easier)
  Use sentences from the lesson only.

### 4. Quiz

Multiple-choice questions testing:

- Vocabulary meaning
- Correct character usage
- Pinyin tone accuracy
- Sentence structure

Each question needs 3-4 options with exactly one correct answer and a brief
explanation of why the correct answer is correct.

## Output Format

Return a JSON object with keys: `flashcards`, `fill_blank`, `translation_drills`,
`quiz`. Each matches the corresponding array schema in `lesson-data.v1.review`.
Do NOT wrap in markdown code fences. Return raw JSON only.
