"""Builds the FAISS index for query_faa_rules from the source FAA orders.

Chunking is section/rule-boundary aware, not fixed-token-window — this is the specific
design goal called out in vector_db_setup_justification.md: a naive fixed-size chunker
would split the wake-turbulence separation matrices (TBL 5-5-1, TBL 5-5-2) mid-row.

Scope decision (see mcp_server/source_docs/README.md for the full rationale): JO 7110.65BB
is ingested in full. JO 7360.1K is ingested only through Chapter 2 — its Appendix A-D decode
tables are dense multi-column tables that `pdftotext` extracts with serious row/column
misalignment (verified by hand against known aircraft, e.g. rows silently absorbing the next
designator's model name). Fabricating a "cleaned up" aircraft-type -> wake-category lookup
from that misaligned extraction would risk silently wrong safety-adjacent data, so it's
deliberately left out rather than guessed at. A real table-extraction pass (pdfplumber with
explicit table detection, applied just to those appendices) is future work, not done here.

Run inside the mcp_server container (needs poppler-utils, faiss-cpu, sentence-transformers):
    docker compose run --rm mcp_server python vector_db/ingest.py
"""

import json
import re
import subprocess
from pathlib import Path

import faiss
from sentence_transformers import SentenceTransformer

SOURCE_DOCS_DIR = Path(__file__).parent.parent / "source_docs"
OUTPUT_DIR = Path(__file__).parent
INDEX_PATH = OUTPUT_DIR / "faa_rules.index"
CHUNKS_PATH = OUTPUT_DIR / "chunks.jsonl"

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# `truncate_before_second` cuts the extracted text at the second occurrence of the given
# marker (the first is always this doc's own table-of-contents entry) — used to drop
# JO 7360.1K's Appendix A-D tables while keeping its Chapter 1-2 prose intact.
DOCS = [
    {
        "doc_id": "JO_7110.65BB",
        "title": "FAA Order JO 7110.65BB, Air Traffic Control",
        "filename": "JO_7110.65BB_Air_Traffic_Control.pdf",
        "truncate_before_second": None,
    },
    {
        "doc_id": "JO_7360.1K",
        "title": "FAA Order JO 7360.1K, Aircraft Type Designators",
        "filename": "JO_7360.1K_Aircraft_Type_Designators.pdf",
        "truncate_before_second": "Appendix A. Decode",
    },
]

# This exact garbled string is JO 7110.65BB's per-page revision watermark, interleaved with
# the page footer by the PDF's text layer — it isn't natural language and adds only noise.
WATERMARK_RE = re.compile(r"^J7JO1O\S*.*$", re.MULTILINE)

# Numbered ATC paragraph headings, e.g. "3-9-6. SAME RUNWAY SEPARATION". Requires the period
# immediately after the number so running-footer repeats of the same number (which lack the
# period, e.g. "3-9-6                    Departure Procedures and Separation") don't match.
NUMBERED_HEADING_RE = re.compile(r"^(\d+-\d+(?:-\d+)?)\.\s+([A-Z][A-Za-z0-9 ,/\-\(\)&'.]{2,100})$")

# Pilot/Controller Glossary entries, e.g. "AIRCRAFT WAKE CATEGORIES- For the purposes of...".
# Includes U+2019 (curly apostrophe) alongside ASCII "'" — pdftotext -enc UTF-8 renders terms
# like "AIRMEN'S..." with the real typographic apostrophe, not the ASCII one.
GLOSSARY_HEADING_RE = re.compile(r"^([A-Z][A-Z0-9 &/,.'’]{1,78})(?: \[ICAO\])?-(\s|$)")

# Recurring document scaffolding that happens to match GLOSSARY_HEADING_RE's shape (ALL CAPS
# WORD-) but isn't a distinct glossary term — without this exclusion, every "REFERENCE-" or
# "NOTE-" callout in the entire document (there are hundreds) starts a fresh chunk labeled
# just "REFERENCE"/"NOTE", scattering content that belongs with its parent numbered paragraph
# and destroying citation quality. Treated as regular body text instead, so it stays attached
# to whichever numbered-paragraph or glossary-term heading is currently open.
BOILERPLATE_LABELS = {"REFERENCE", "NOTE", "CAUTION", "EXAMPLE", "PHRASEOLOGY"}

MIN_CHUNK_CHARS = 40
MAX_CHUNK_CHARS = 1600


def extract_pdf_text(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def clean_text(text: str) -> str:
    text = WATERMARK_RE.sub("", text)
    # With -enc UTF-8, pdftotext faithfully renders the PDF's actual paragraph-numbering
    # character, which is an en dash (U+2013) rather than a plain hyphen — e.g. the real text
    # is "3–10–3." not "3-10-3." Normalize dash variants to ASCII hyphen so the
    # heading regexes below (and anyone grepping the corpus) don't have to special-case this.
    text = text.replace("–", "-").replace("−", "-")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def truncate_before_second_occurrence(text: str, marker: str) -> str:
    first = text.find(marker)
    if first == -1:
        return text
    second = text.find(marker, first + len(marker))
    return text[:second] if second != -1 else text


def split_long_chunk(chunk: dict) -> list[dict]:
    text = chunk["text"]
    if len(text) <= MAX_CHUNK_CHARS:
        return [chunk]

    paragraphs = text.split("\n\n")
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) > MAX_CHUNK_CHARS:
            pieces.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        pieces.append(buf)

    if len(pieces) == 1:
        return [chunk]

    out = []
    for i, piece in enumerate(pieces):
        suffix = chr(ord("a") + i)
        out.append({**chunk, "section_id": f"{chunk['section_id']}{suffix}", "text": piece})
    return out


def chunk_document(doc_id: str, title: str, text: str) -> list[dict]:
    lines = text.split("\n")
    chunks: list[dict] = []
    current: dict | None = None

    def flush():
        nonlocal current
        if current and len(current["text"].strip()) >= MIN_CHUNK_CHARS:
            chunks.append(current)
        current = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if current is not None:
                current["text"] += "\n"
            continue

        heading_match = NUMBERED_HEADING_RE.match(stripped)
        glossary_match = None if heading_match else GLOSSARY_HEADING_RE.match(stripped)
        if glossary_match and glossary_match.group(1).strip() in BOILERPLATE_LABELS:
            glossary_match = None

        if heading_match:
            flush()
            section_id, heading = heading_match.group(1), heading_match.group(2).strip()
            current = {
                "doc_id": doc_id,
                "doc_title": title,
                "section_id": section_id,
                "heading": heading,
                "text": stripped + "\n",
            }
        elif glossary_match:
            flush()
            term = glossary_match.group(1).strip()
            current = {
                "doc_id": doc_id,
                "doc_title": title,
                "section_id": term,
                "heading": term,
                "text": stripped + "\n",
            }
        else:
            if current is None:
                current = {
                    "doc_id": doc_id,
                    "doc_title": title,
                    "section_id": "FRONT_MATTER",
                    "heading": "Front matter",
                    "text": "",
                }
            current["text"] += stripped + "\n"

    flush()

    out: list[dict] = []
    for c in chunks:
        out.extend(split_long_chunk(c))
    return out


def build_index(chunks: list[dict]) -> None:
    print(f"Embedding {len(chunks)} chunks with {EMBEDDING_MODEL}...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    # Prepending the heading nudges the embedding toward the section's topic, which helps
    # short/table-heavy chunks (e.g. a bare TBL 5-5-1 row block) retrieve correctly.
    texts = [f"{c['heading']}. {c['text']}" for c in chunks]
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=True, normalize_embeddings=True)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)  # inner product over normalized vectors == cosine similarity
    index.add(embeddings.astype("float32"))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_PATH))
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Wrote {INDEX_PATH} and {CHUNKS_PATH}")


def main() -> None:
    all_chunks: list[dict] = []
    for doc in DOCS:
        pdf_path = SOURCE_DOCS_DIR / doc["filename"]
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"{pdf_path} not found — see mcp_server/source_docs/README.md for how to obtain it"
            )
        print(f"Extracting {pdf_path.name}...")
        text = extract_pdf_text(pdf_path)
        text = clean_text(text)
        if doc["truncate_before_second"]:
            text = truncate_before_second_occurrence(text, doc["truncate_before_second"])
        doc_chunks = chunk_document(doc["doc_id"], doc["title"], text)
        print(f"  {len(doc_chunks)} chunks from {doc['doc_id']}")
        all_chunks.extend(doc_chunks)

    for i, c in enumerate(all_chunks):
        c["chunk_id"] = i

    build_index(all_chunks)


if __name__ == "__main__":
    main()
