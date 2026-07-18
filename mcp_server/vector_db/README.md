# Vector DB build artifacts

`faa_rules.index` (FAISS) and `chunks.jsonl` (chunk text + section citations, one JSON object
per line, indexed positionally to match the FAISS index) are gitignored build outputs, not
hand-authored files. Regenerate them with:

```
docker compose run --rm mcp_server python vector_db/ingest.py
```

Requires the two source PDFs to be present in `mcp_server/source_docs/` first — see that
directory's README.
