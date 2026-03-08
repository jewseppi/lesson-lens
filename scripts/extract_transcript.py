"""
extract_transcript.py — Input adapter for chat export files.

Reads a .txt chat export and produces a normalized list of raw lines
with provenance metadata. Designed to be extended for PDF input later.
"""
import argparse
import hashlib
import os
import sys


def compute_file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_txt(filepath: str) -> dict:
    """Read a UTF-8 text chat export and return structured metadata + lines."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw_text = f.read()

    lines = raw_text.splitlines()

    return {
        "file_name": os.path.basename(filepath),
        "file_path": os.path.abspath(filepath),
        "file_hash_sha256": compute_file_hash(filepath),
        "encoding": "utf-8",
        "line_count": len(lines),
        "lines": lines,
    }


def extract(filepath: str) -> dict:
    """Route to the appropriate extractor based on file extension."""
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".txt", ""):
        # Treat extensionless files as txt (LINE exports sometimes have no ext)
        return extract_txt(filepath)
    elif ext == ".pdf":
        raise NotImplementedError(
            "PDF extraction not yet implemented. "
            "Export your chat as .txt for now."
        )
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def main():
    parser = argparse.ArgumentParser(description="Extract transcript from chat export file")
    parser.add_argument("--input", required=True, help="Path to chat export file")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    result = extract(args.input)
    print(f"Extracted {result['line_count']} lines from {result['file_name']}")
    print(f"SHA256: {result['file_hash_sha256']}")


if __name__ == "__main__":
    main()
