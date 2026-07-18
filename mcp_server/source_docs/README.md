# Source documents

Gitignored — both are large (10-12 MB), public-domain FAA orders, regenerable by downloading
again rather than committing. `mcp_server/vector_db/ingest.py` expects exactly these two files
here, named exactly as below:

- `JO_7110.65BB_Air_Traffic_Control.pdf` — FAA Order JO 7110.65 (Air Traffic Control), current
  edition. Free download from the FAA's Air Traffic Organization publications page
  (search "FAA Order JO 7110.65"). Covers wake turbulence separation (Ch. 5, incorporating
  the former JO 7110.126 Consolidated Wake Turbulence standard — categories A-I plus TBL 5-5-1/
  TBL 5-5-2), NTZ/simultaneous close parallel approach breakout procedures (Ch. 5), and
  same-runway departure interval timing (Para 3-9-6).
- `JO_7360.1K_Aircraft_Type_Designators.pdf` — FAA Order JO 7360.1K (Aircraft Type Designators).
  Only Chapters 1-2 (definitions of WTC/CWT/SRS/LAHSO) are ingested — see the docstring in
  `ingest.py` for why the Appendix A-D decode tables are deliberately excluded.

To rebuild the index after updating either PDF:
```
docker compose run --rm mcp_server python vector_db/ingest.py
```
