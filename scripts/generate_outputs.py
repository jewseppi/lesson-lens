"""
generate_outputs.py — LLM-powered lesson summarizer and asset generator.

Takes sessions.json (or a single session) and produces lesson-data.json,
Markdown summary, HTML viewer, and flashcards CSV via LLM API calls.

Supports OpenAI, Anthropic (Claude), and Gemini. Provider/model can be
overridden per run via CLI flags.
"""
import argparse
import base64
import csv
import json
import os
import sys
from datetime import datetime, timezone

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: str | None = None) -> dict:
    default = os.path.join(os.path.dirname(__file__), "..", "config", "pipeline.yaml")
    path = config_path or default
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts", f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# LLM provider adapters
# ---------------------------------------------------------------------------

def _encode_image(path: str) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type)."""
    ext = os.path.splitext(path)[1].lower()
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_types.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), media_type


def call_openai(prompt: str, user_content: str, model: str, temperature: float,
                image_paths: list[str] | None = None) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = OpenAI()  # uses OPENAI_API_KEY env var

    # Build multimodal content if images are provided
    if image_paths:
        content_parts: list[dict] = [{"type": "text", "text": user_content}]
        for img_path in image_paths:
            b64, mtype = _encode_image(img_path)
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mtype};base64,{b64}"},
            })
        user_msg: dict = {"role": "user", "content": content_parts}
    else:
        user_msg = {"role": "user", "content": user_content}

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[{"role": "system", "content": prompt}, user_msg],
    )
    return response.choices[0].message.content


def call_anthropic(prompt: str, user_content: str, model: str, temperature: float,
                   image_paths: list[str] | None = None) -> str:
    try:
        import anthropic
    except ImportError:
        print("Error: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

    # Build multimodal content if images are provided
    if image_paths:
        content_parts: list[dict] = [{"type": "text", "text": user_content}]
        for img_path in image_paths:
            b64, mtype = _encode_image(img_path)
            content_parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mtype, "data": b64},
            })
        user_msg_content: str | list = content_parts
    else:
        user_msg_content = user_content

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=temperature,
        system=prompt,
        messages=[{"role": "user", "content": user_msg_content}],
    )
    return response.content[0].text


def call_gemini(prompt: str, user_content: str, model: str, temperature: float,
                image_paths: list[str] | None = None) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        print(
            "Error: google-generativeai package not installed. Run: pip install google-generativeai",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print(
            "Error: GEMINI_API_KEY or GOOGLE_API_KEY not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    genai.configure(api_key=api_key)
    client = genai.GenerativeModel(model_name=model, system_instruction=prompt)
    response = client.generate_content(
        user_content,
        generation_config={"temperature": temperature},
    )
    return response.text


def call_ollama(prompt: str, user_content: str, model: str, temperature: float,
                image_paths: list[str] | None = None) -> str:
    import json as _json
    from urllib import error as _urlerr
    from urllib import request as _urlreq

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    payload = _json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }).encode()

    req = _urlreq.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _urlreq.urlopen(req, timeout=600) as resp:
            data = _json.loads(resp.read())
    except _urlerr.URLError as exc:
        print(f"Error: cannot reach Ollama at {base_url}: {exc}", file=sys.stderr)
        sys.exit(1)

    return data["message"]["content"]


def call_openai_compatible_local(prompt: str, user_content: str, model: str, temperature: float,
                                image_paths: list[str] | None = None) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    base_url = os.environ.get("LOCAL_OAI_BASE_URL", "http://localhost:1234/v1")
    client = OpenAI(api_key="not-needed", base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


PROVIDERS = {
    "openai": call_openai,
    "anthropic": call_anthropic,
    "gemini": call_gemini,
    "ollama": call_ollama,
    "openai_compatible_local": call_openai_compatible_local,
}


# ---------------------------------------------------------------------------
# Transcript builder — assembles the user prompt for the LLM
# ---------------------------------------------------------------------------

def build_transcript_text(session: dict, reclassifications: dict | None = None,
                          attachments: list[dict] | None = None) -> str:
    """Build a readable transcript block from a session for the LLM.

    Args:
        session: Session dict with messages array.
        reclassifications: Optional dict mapping message_id -> corrected message_type.
            When provided, reclassified messages are annotated in the transcript.
        attachments: Optional list of attachment dicts with 'original_filename' and
            optionally 'ocr_text'. When provided, media-reference messages are
            annotated with attachment info instead of being skipped.
    """
    lines = []
    lines.append(f"## Lesson: {session['date']} ({session['start_time']}–{session['end_time']})")
    lines.append(f"Session ID: {session['session_id']}")
    lines.append(f"Messages: {session['message_count']} total, {session['lesson_content_count']} lesson-content")
    if attachments:
        lines.append(f"Attached images: {len(attachments)}")
    lines.append("")

    reclass = reclassifications or {}

    # Track which attachment to associate with each media-reference message
    att_iter = iter(attachments or [])

    for msg in session.get("messages", []):
        role = msg.get("speaker_role", "unknown")
        raw = msg.get("text_raw", "")
        mid = msg.get("message_id", "")
        mtype = msg.get("message_type", "")
        time_str = msg.get("time", "")

        if mtype == "call-system":
            continue  # skip system call notifications

        label = "Teacher" if role == "teacher" else ("Student" if role == "student" else "?")

        if mtype == "media-reference":
            # Include media-reference with attachment context instead of skipping
            att = next(att_iter, None)
            if att:
                fname = att.get("original_filename", "image")
                line = f"[{mid}] {time_str} {label}: [Sent image: {fname}]"
                ocr = att.get("ocr_text")
                if ocr:
                    line += f" [Image text: {ocr}]"
            else:
                line = f"[{mid}] {time_str} {label}: [Sent image]"
            lines.append(line)
            continue

        line = f"[{mid}] {time_str} {label}: {raw}"

        # Annotate reclassified messages so the LLM knows to include/exclude them
        if mid in reclass and reclass[mid] != mtype:
            line += f" [RECLASSIFIED: was {mtype} → {reclass[mid]}]"

        lines.append(line)

    return "\n".join(lines)


def build_correction_context(corrections: list) -> str:
    """Format accepted corrections as a context block for the LLM.

    Args:
        corrections: List of correction dicts, each with keys like:
            - type: 'reclassify_message' | 'translation' | 'pinyin' | 'annotation'
            - message_id / item_id: target identifier
            - original / corrected: original and corrected values
            - detail: extra context
    """
    if not corrections:
        return ""

    lines = [
        "## Ground Truth Corrections",
        "The following corrections have been verified by the user. Treat them as authoritative:",
        "",
    ]

    for c in corrections:
        ctype = c.get("type", "")
        if ctype == "reclassify_message":
            lines.append(
                f"- Message {c.get('message_id', '?')}: reclassified from "
                f"'{c.get('original', '?')}' to '{c.get('corrected', '?')}'"
            )
        elif ctype == "translation":
            lines.append(
                f"- {c.get('item_id', 'Item')}: English should be "
                f"\"{c.get('corrected', '')}\" not \"{c.get('original', '')}\""
            )
        elif ctype == "pinyin":
            lines.append(
                f"- {c.get('item_id', 'Item')}: pinyin should be "
                f"\"{c.get('corrected', '')}\" not \"{c.get('original', '')}\""
            )
        elif ctype == "annotation":
            lines.append(f"- {c.get('detail', c.get('message_id', 'Note'))}")
        else:
            detail = c.get("detail") or c.get("corrected") or str(c)
            lines.append(f"- {detail}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output generators (from lesson-data JSON)
# ---------------------------------------------------------------------------

def generate_markdown(lesson: dict, output_path: str):
    """Write a Markdown study summary from lesson-data."""
    lines = []
    lines.append(f"# {lesson.get('title', 'Lesson Summary')}")
    lines.append(f"**Date:** {lesson.get('lesson_date', '')}")
    lines.append("")

    # Overview
    summary = lesson.get("summary", {})
    lines.append("## Overview")
    lines.append(summary.get("overview", ""))
    lines.append("")

    # Key Sentences
    lines.append("## Key Sentences")
    for ks in lesson.get("key_sentences", []):
        lines.append(f"- **{ks.get('zh', '')}**")
        lines.append(f"  {ks.get('pinyin', '')}")
        lines.append(f"  {ks.get('en', '')}")
        if ks.get("context_note"):
            lines.append(f"  *{ks['context_note']}*")
        lines.append("")

    # Vocabulary
    lines.append("## Vocabulary")
    lines.append("| Term | Pinyin | English | Type |")
    lines.append("|------|--------|---------|------|")
    for v in lesson.get("vocabulary", []):
        lines.append(f"| {v.get('term_zh','')} | {v.get('pinyin','')} | {v.get('en','')} | {v.get('pos_or_type','')} |")
    lines.append("")

    # Corrections
    corrections = lesson.get("corrections", [])
    if corrections:
        lines.append("## Teacher Corrections")
        for c in corrections:
            lines.append(f"- ~~{c.get('learner_original', '')}~~ → **{c.get('teacher_correction', '')}**")
            lines.append(f"  {c.get('reason', '')}")
            lines.append("")

    # Usage notes
    if summary.get("usage_notes"):
        lines.append("## Usage / Context Notes")
        lines.append(summary["usage_notes"])
        lines.append("")

    # Recap
    if summary.get("short_recap"):
        lines.append("## Quick Recap")
        lines.append(summary["short_recap"])
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_html(lesson: dict, output_path: str):
    """Write a mobile-friendly HTML study page from lesson-data."""
    title = lesson.get("title", "Lesson Summary")
    date = lesson.get("lesson_date", "")
    summary = lesson.get("summary", {})

    # Build key sentences HTML
    ks_items = ""
    for ks in lesson.get("key_sentences", []):
        ks_items += f"""<div class="card">
  <div class="zh">{_esc(ks.get('zh',''))}</div>
  <div class="pinyin">{_esc(ks.get('pinyin',''))}</div>
  <div class="en">{_esc(ks.get('en',''))}</div>
</div>\n"""

    # Build vocabulary rows
    vocab_rows = ""
    for v in lesson.get("vocabulary", []):
        vocab_rows += f"<tr><td>{_esc(v.get('term_zh',''))}</td><td>{_esc(v.get('pinyin',''))}</td><td>{_esc(v.get('en',''))}</td><td>{_esc(v.get('pos_or_type',''))}</td></tr>\n"

    # Build corrections
    corr_items = ""
    for c in lesson.get("corrections", []):
        corr_items += f"""<div class="card">
  <div class="strike">{_esc(c.get('learner_original',''))}</div>
  <div class="correct">→ {_esc(c.get('teacher_correction',''))}</div>
  <div class="reason">{_esc(c.get('reason',''))}</div>
</div>\n"""

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 640px; margin: 0 auto; padding: 16px; background: #fafafa; color: #333; }}
  h1 {{ font-size: 1.4em; margin-bottom: 4px; }}
  .date {{ color: #666; margin-bottom: 16px; }}
  h2 {{ font-size: 1.1em; margin: 20px 0 8px; border-bottom: 2px solid #4a90d9; padding-bottom: 4px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 12px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .zh {{ font-size: 1.3em; font-weight: bold; }}
  .pinyin {{ color: #666; font-style: italic; }}
  .en {{ color: #444; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
  th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #eee; font-size: 0.9em; }}
  th {{ background: #f0f0f0; }}
  .strike {{ text-decoration: line-through; color: #c33; }}
  .correct {{ color: #2a7; font-weight: bold; }}
  .reason {{ color: #666; font-size: 0.9em; margin-top: 4px; }}
  .overview, .recap {{ background: #fff; border-radius: 8px; padding: 12px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<div class="date">{_esc(date)}</div>

<h2>Overview</h2>
<div class="overview">{_esc(summary.get('overview', ''))}</div>

<h2>Key Sentences</h2>
{ks_items}

<h2>Vocabulary</h2>
<table>
<tr><th>Term</th><th>Pinyin</th><th>English</th><th>Type</th></tr>
{vocab_rows}
</table>

<h2>Corrections</h2>
{corr_items if corr_items else '<p>No corrections this lesson.</p>'}

<h2>Usage Notes</h2>
<div class="overview">{_esc(summary.get('usage_notes', ''))}</div>

<h2>Quick Recap</h2>
<div class="recap">{_esc(summary.get('short_recap', ''))}</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def _esc(text: str) -> str:
    """HTML-escape text."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_csv(lesson: dict, output_path: str):
    """Write flashcards CSV (importable into Anki)."""
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Front", "Back", "Hint", "Tags"])

        # Vocabulary cards
        for v in lesson.get("vocabulary", []):
            front = v.get("term_zh", "")
            back = f"{v.get('pinyin', '')} — {v.get('en', '')}"
            hint = v.get("pos_or_type", "")
            writer.writerow([front, back, hint, "vocab"])

        # Key sentence cards
        for ks in lesson.get("key_sentences", []):
            front = ks.get("zh", "")
            back = f"{ks.get('pinyin', '')} — {ks.get('en', '')}"
            writer.writerow([front, back, "", "sentence"])

        # Review flashcards
        for fc in lesson.get("review", {}).get("flashcards", []):
            writer.writerow([fc.get("front", ""), fc.get("back", ""),
                             fc.get("hint", ""), "review"])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def process_session(session: dict, config: dict, provider: str, model: str,
                    temperature: float, run_id: str, output_base: str,
                    corrections: list | None = None,
                    retrieval_context: str | None = None,
                    attachments: list[dict] | None = None) -> dict:
    """Run the full generation pipeline for one session.

    Args:
        corrections: Optional list of verified corrections to inject as context.
            Each is a dict with keys: type, message_id/item_id, original, corrected, detail.
        retrieval_context: Optional text block of prior knowledge from retrieval index.
        attachments: Optional list of attachment dicts with 'original_filename',
            'stored_filename' (full path), and optionally 'ocr_text'.
            Images are sent to vision-capable providers (openai, anthropic).
    """
    session_id = session["session_id"]
    date = session["date"]
    output_dir = os.path.join(output_base, session_id)
    os.makedirs(output_dir, exist_ok=True)

    call_llm = PROVIDERS.get(provider)
    if not call_llm:
        print(f"Error: unknown provider '{provider}'. Supported: {list(PROVIDERS.keys())}", file=sys.stderr)
        sys.exit(1)

    # Build reclassification lookup for transcript annotation
    reclassifications = {}
    if corrections:
        for c in corrections:
            if c.get("type") == "reclassify_message" and c.get("message_id"):
                reclassifications[c["message_id"]] = c.get("corrected", "")

    # --- Pass 1: Master summarizer ---
    print(f"\n[{session_id}] Pass 1: Generating lesson summary...")
    master_prompt = load_prompt("master-summarizer")
    transcript_text = build_transcript_text(session, reclassifications=reclassifications,
                                            attachments=attachments)

    # Prepend retrieval context from prior sessions (Phase 4)
    if retrieval_context:
        transcript_text = retrieval_context + "\n" + transcript_text

    # Prepend correction context if available
    correction_context = build_correction_context(corrections) if corrections else ""
    if correction_context:
        transcript_text = correction_context + "\n" + transcript_text

    # Collect image paths for vision-capable providers
    image_paths: list[str] = []
    if attachments and provider in ("openai", "anthropic"):
        for att in attachments:
            path = att.get("stored_path")
            if path and os.path.isfile(path):
                image_paths.append(path)
        if image_paths:
            print(f"[{session_id}] Including {len(image_paths)} image(s) for vision...")

    raw_summary = call_llm(master_prompt, transcript_text, model, temperature,
                           image_paths=image_paths if image_paths else None)

    # Parse JSON response
    lesson_data = _parse_llm_json(raw_summary, "master-summarizer")

    # Inject/override metadata
    lesson_data["schema_version"] = "lesson-data.v1"
    lesson_data["lesson_id"] = f"lesson-{session_id}"
    lesson_data["lesson_date"] = date
    lesson_data.setdefault("source_session_ids", [session_id])
    lesson_data.setdefault("language_mode", {
        "script": config.get("language", {}).get("script", "traditional"),
        "pinyin_policy": config.get("language", {}).get("pinyin_policy", "every_line"),
        "translation_language": config.get("language", {}).get("translation_language", "english"),
    })

    # --- Pass 2: Secondary assets ---
    print(f"[{session_id}] Pass 2: Generating review exercises...")
    secondary_prompt = load_prompt("secondary-assets")
    # Feed the summary data to the secondary prompt
    secondary_input = json.dumps({
        "key_sentences": lesson_data.get("key_sentences", []),
        "vocabulary": lesson_data.get("vocabulary", []),
        "corrections": lesson_data.get("corrections", []),
    }, ensure_ascii=False)
    raw_review = call_llm(secondary_prompt, secondary_input, model, temperature)
    review_data = _parse_llm_json(raw_review, "secondary-assets")
    lesson_data.setdefault("review", {})
    lesson_data["review"].update(review_data)

    # --- Generation metadata ---
    lesson_data["generation_meta"] = {
        "provider": provider,
        "model": model,
        "prompt_version": config.get("generation", {}).get("prompt_version", "v1"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "temperature": temperature,
    }

    # --- Write outputs ---
    # JSON
    json_path = os.path.join(output_dir, "lesson-data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(lesson_data, f, ensure_ascii=False, indent=2)

    # Markdown
    md_path = os.path.join(output_dir, "summary.md")
    generate_markdown(lesson_data, md_path)

    # HTML
    html_path = os.path.join(output_dir, "summary.html")
    generate_html(lesson_data, html_path)

    # CSV
    csv_path = os.path.join(output_dir, "flashcards.csv")
    generate_csv(lesson_data, csv_path)

    # Update asset paths in lesson data
    lesson_data["assets"] = {
        "markdown_path": "summary.md",
        "html_path": "summary.html",
        "flashcards_csv_path": "flashcards.csv",
    }
    # Re-write JSON with asset paths
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(lesson_data, f, ensure_ascii=False, indent=2)

    print(f"[{session_id}] Done → {output_dir}")
    return {"session_id": session_id, "output_dir": output_dir, "files": [
        json_path, md_path, html_path, csv_path
    ]}


def _parse_llm_json(raw: str, label: str) -> dict:
    """Parse JSON from LLM response, stripping code fences if present."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse {label} JSON: {e}", file=sys.stderr)
        print(f"Raw response (first 500 chars): {text[:500]}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate lesson study packages from parsed sessions")
    parser.add_argument("--sessions", required=True, help="Path to sessions.json")
    parser.add_argument("--session-id", default=None, help="Process specific session ID (default: all)")
    parser.add_argument("--provider", default=None, help="LLM provider: openai, anthropic, or gemini")
    parser.add_argument("--model", default=None, help="Model name override")
    parser.add_argument("--temperature", type=float, default=None, help="Temperature override")
    parser.add_argument("--run-id", default=None, help="Run ID (default: auto)")
    parser.add_argument("--output-dir", default=None, help="Output base dir override")
    parser.add_argument("--config", default=None, help="Path to pipeline.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print transcript but skip LLM calls")
    args = parser.parse_args()

    if not os.path.isfile(args.sessions):
        print(f"Error: file not found: {args.sessions}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    gen_config = config.get("generation", {})

    provider = args.provider or gen_config.get("default_provider", "openai")
    model = args.model or gen_config.get("default_model", "gpt-4o")
    temperature = args.temperature if args.temperature is not None else gen_config.get("temperature", 0.3)
    run_id = args.run_id or datetime.now().strftime("%Y-%m-%d_%H%M%S")

    base_dir = os.path.join(os.path.dirname(__file__), "..")
    output_base = args.output_dir or os.path.join(base_dir, "summaries", run_id)

    with open(args.sessions, "r", encoding="utf-8") as f:
        data = json.load(f)

    sessions = data.get("sessions", [])
    if args.session_id:
        sessions = [s for s in sessions if s["session_id"] == args.session_id]
        if not sessions:
            print(f"Error: session '{args.session_id}' not found", file=sys.stderr)
            sys.exit(1)

    print(f"Provider: {provider} / {model} (temp={temperature})")
    print(f"Sessions to process: {len(sessions)}")

    if args.dry_run:
        for sess in sessions:
            print(f"\n{'='*60}")
            print(build_transcript_text(sess))
        print(f"\n{'='*60}")
        print("DRY RUN — no LLM calls made.")
        return

    results = []
    for sess in sessions:
        result = process_session(sess, config, provider, model, temperature, run_id, output_base)
        results.append(result)

    print(f"\n{'='*50}")
    print(f"Generation complete: {len(results)} session(s)")
    print(f"Output base: {output_base}")
    for r in results:
        print(f"  {r['session_id']}: {len(r['files'])} files")


if __name__ == "__main__":
    main()
