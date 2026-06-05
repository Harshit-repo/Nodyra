# Python-Powered Unique Nodes — Design Spec

> Date: 2026-06-05
> Status: approved for implementation planning
> Scope: a comprehensive catalog of nodes that exploit Noodle's Python-native runtime to do things n8n cannot do at all and Windmill only exposes as raw scripts. Covers document intelligence, browser automation, statistical analysis, geospatial, audio/media, scientific verticals, data quality, and security automation.

## Positioning

Noodle's differentiator is "n8n-style visual wiring on top of a real warm Python runtime." This catalog is the proof: workflows that can run a headless browser, extract tables from a PDF, transcribe audio without an API key, solve an optimization problem, and generate a Word report — all wired visually, no code required.

Neither n8n (TypeScript/Node.js) nor Windmill (script-first, cold workers) exposes these capabilities as first-class visual nodes. This catalog is Noodle's competitive moat.

## Hard Invariant: Packages Are Never Global

**No package in this catalog ships in Noodle's global environment.**

Every node:

1. Declares `requirements=[...]` on its `@node` decorator.
2. Lazy-imports heavy packages inside the function body with a friendly `RuntimeError` if the package is absent.
3. Returns large outputs as artifact refs or DatasetRefs — never inline JSON blobs.

When a user adds a node whose packages are not installed in the current environment, the editor shows a missing-package banner with an "Add to environment" action. The API preflight blocks runs until requirements are met. This is the same mechanism already used by all AI/ML nodes.

```python
@node(
    name="PDF Extract Tables",
    id="pdf_extract_tables",
    category="Document Intelligence",
    requirements=["pdfplumber>=0.11", "pandas>=2.0"],
)
def pdf_extract_tables(input=None, ...):
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "This node requires pdfplumber. Add it to the workflow "
            "environment, rebuild, then run again."
        ) from exc
```

This invariant applies to every single node in this spec without exception.

## Killer Nodes

These 12 nodes are the marketing headline — the things that make someone choose Noodle over n8n. Each is impossible in n8n natively and requires raw scripting in Windmill.

| # | Node ID | Package(s) | Pitch |
|---|---------|-----------|-------|
| 1 | `pdf_extract_tables` | `pdfplumber`, `camelot-py` | Extract tables from any PDF to a DatasetRef |
| 2 | `pdf_generate` | `weasyprint` / `fpdf2` | Render an HTML/Jinja template to a PDF artifact |
| 3 | `docx_generate` | `python-docx` | Generate a Word document from a template + data |
| 4 | `excel_report_generate` | `openpyxl` | Build an Excel report with charts, formatting, named ranges |
| 5 | `browser_scrape` | `playwright` | Navigate a real headless browser, extract structured data |
| 6 | `browser_screenshot` | `playwright` | Screenshot any URL or CSS selector to an image artifact |
| 7 | `whisper_transcribe_local` | `faster-whisper` | Transcribe audio/video locally — no API key, no data leaves the host |
| 8 | `statistical_test` | `scipy` | t-test, chi-square, ANOVA, Mann-Whitney, KS — p-value + plain-English interpretation |
| 9 | `time_series_forecast` | `prophet` / `pmdarima` | Forecast a time series column with confidence intervals |
| 10 | `map_generate` | `folium` | Interactive HTML map artifact with markers or choropleth |
| 11 | `optimization_solve` | `pulp` / `ortools` | Solve a linear or integer program defined as JSON constraints |
| 12 | `data_profile_report` | `ydata-profiling` | Full HTML data profiling report artifact from a DatasetRef |

## File Layout

```text
packages/nodes/noodle_nodes/
  document_intelligence.py    # PDF, Word, Excel, OCR, barcodes
  browser_automation.py       # Playwright, HTML extraction, feeds
  statistical_analysis.py     # scipy, statsmodels, prophet, optimization
  geospatial.py               # geopandas, folium, geopy
  media_processing.py         # audio, image, video
  domain_verticals.py         # finance, bioinformatics, chemistry, graphs, symbolic
  data_quality.py             # pandera, great_expectations, profiling, fuzzy match
  security_automation.py      # cryptography, JWT, LDAP, SFTP, PGP
```

Each file registers its nodes in `packages/nodes/noodle_nodes/__init__.py`. Tests live in `packages/nodes/tests/test_<module>.py`.

## Category 1 — Document Intelligence

### Overview

Python has the richest document processing ecosystem of any language. n8n has almost nothing here. These nodes let users build document pipelines visually: extract → transform → generate → distribute.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `pdf_extract_text` | Extract text with layout preservation (page, bbox, font) to records | `pdfplumber` |
| `pdf_extract_tables` ★ | Extract tables from PDF to DatasetRef; auto-detect lattice vs stream layout | `pdfplumber>=0.11`, `camelot-py`, `pandas>=2.0` |
| `pdf_extract_images` | Extract embedded images as artifact refs | `pymupdf` |
| `pdf_generate` ★ | Render Jinja HTML template + data → PDF artifact | `weasyprint` or `fpdf2` |
| `pdf_merge_split` | Merge multiple PDF artifacts or split by page range | `pypdf` |
| `pdf_fill_form` | Fill AcroForm fields by name dict, return filled PDF artifact | `pypdf` |
| `pdf_watermark` | Stamp text or image watermark on every page | `pypdf`, `reportlab` |
| `html_to_pdf` | Convert HTML string or URL to PDF artifact | `weasyprint` |
| `docx_generate` ★ | Populate a `.docx` Jinja template with data, return artifact | `python-docx` |
| `docx_extract` | Extract paragraphs, tables, headings from Word artifact to records | `python-docx` |
| `excel_report_generate` ★ | Build `.xlsx` with charts, conditional formatting, named ranges from DatasetRef | `openpyxl` |
| `excel_extract` | Read a sheet, named range, or table from `.xlsx` artifact to DatasetRef | `openpyxl` |
| `pptx_generate` | Build a PowerPoint from a slide template + records | `python-pptx` |
| `ocr_document` | OCR a scanned PDF or image artifact to text + layout artifact | `pytesseract` or `easyocr` |
| `document_parse_layout` | Parse document structure: headings, tables, figures, sections with coordinates | `docling` |
| `barcode_qr_generate` | Generate QR code or 1D barcode to image artifact | `qrcode`, `python-barcode` |
| `barcode_qr_decode` | Decode barcode/QR code from image artifact | `pyzbar` |
| `spreadsheet_diff` | Compare two spreadsheet artifacts; output changed/added/removed rows as DatasetRef | `openpyxl`, `pandas` |

### Output contracts

- Text extraction → `{"text": str, "pages": int, "artifact": ArtifactRef}` for long text; inline for short.
- Table extraction → DatasetRef (one per detected table) plus `{"tables_found": int, "page": int}` summary.
- File generation → ArtifactRef with `content_type` set correctly (`application/pdf`, `application/vnd.openxmlformats-officedocument.*`).

## Category 2 — Browser & Web Automation

### Overview

Playwright gives Noodle a real headless browser. This lets workflows scrape JS-rendered SPAs, automate login flows, take screenshots, and print pages to PDF — none of which n8n's HTTP Request node can do.

`playwright install chromium` must be run in the environment after installing the package. The missing-package banner should surface this as an additional setup step.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `browser_screenshot` ★ | Screenshot URL or CSS selector to image artifact; supports full-page and clip rect | `playwright` |
| `browser_scrape` ★ | Navigate to URL, wait for selector, extract DOM data to records; supports pagination | `playwright` |
| `browser_click_fill` | Click an element, type text into fields, submit a form | `playwright` |
| `browser_pdf_from_url` | Print a web page to PDF artifact using browser print engine | `playwright` |
| `browser_wait_download` | Trigger a download action, capture the downloaded file as artifact | `playwright` |
| `browser_auth_session` | Run a login flow, persist session cookies/storage for downstream browser nodes | `playwright` |
| `html_extract` | CSS or XPath selector extraction from an HTML string to records | `beautifulsoup4` |
| `web_feed_parse` | Parse RSS/Atom feed URL to records | `feedparser` |
| `sitemap_crawl` | Fetch sitemap.xml and return URL list as records | `requests` |
| `graphql_request` | Execute a GraphQL query or mutation against an endpoint | `gql`, `requests` |
| `websocket_send_receive` | Open a WebSocket connection, send a message, collect response | `websockets` |

### Notes

- Playwright browser nodes must be marked `tool_side_effecting=True`.
- Session artifacts from `browser_auth_session` contain cookies/localStorage; they must be handled with the same redaction care as credentials.
- `browser_scrape` should support a `selectors` config param (CSS selector → field name mapping) to define the output schema visually.

## Category 3 — Statistical Analysis & Data Science

### Overview

scipy, statsmodels, prophet, pulp, and umap-learn give Noodle a statistical analysis surface that no visual automation platform has. Users can run hypothesis tests, forecast time series, solve optimization problems, and visualize embeddings — all as workflow nodes consuming DatasetRefs.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `statistical_test` ★ | t-test, chi-square, ANOVA, Mann-Whitney U, Kolmogorov-Smirnov — outputs p-value, statistic, and a plain-English interpretation string | `scipy` |
| `distribution_fit` | Fit a named distribution to a column; output parameters and goodness-of-fit stats | `scipy` |
| `correlation_analysis` | Pearson/Spearman/Kendall correlation matrix → DatasetRef + heatmap artifact | `scipy`, `pandas` |
| `regression_analysis` | OLS or GLM with full summary: coefficients, p-values, R², confidence intervals | `statsmodels` |
| `time_series_forecast` ★ | Forecast a time series column N steps ahead; outputs predictions + lower/upper CI DatasetRef | `prophet` or `pmdarima` |
| `time_series_decompose` | Trend/seasonal/residual decomposition → DatasetRef + chart artifact | `statsmodels` |
| `seasonality_detect` | Detect dominant periods and seasonal patterns in a time column | `statsmodels` |
| `monte_carlo_simulate` | Run N Monte Carlo iterations with configurable input distributions; output result DatasetRef | `numpy` |
| `bootstrap_ci` | Bootstrap confidence interval for any column statistic | `scipy` |
| `survival_analysis` | Kaplan-Meier curves, Cox PH model, log-rank test from event/duration columns | `lifelines` |
| `optimization_solve` ★ | Define LP/MIP as JSON variables + constraints + objective; solve; return solution + status + shadow prices | `pulp` or `ortools` |
| `sensitivity_analysis` | Sobol or Morris global sensitivity indices for a model function | `SALib` |
| `embedding_visualize` ★ | UMAP or t-SNE reduction of an embedding column → 2D/3D scatter plot artifact | `umap-learn`, `scikit-learn` |
| `dimensionality_reduce` | PCA/UMAP/t-SNE → reduced-column DatasetRef (for downstream ML nodes) | `scikit-learn`, `umap-learn` |

### Output contracts

- Hypothesis tests: `{"statistic": float, "p_value": float, "reject_null": bool, "interpretation": str, "test": str, "alpha": float}`.
- Forecasts: DatasetRef with columns `[ds, yhat, yhat_lower, yhat_upper]` plus summary `{"horizon": int, "model": str}`.
- Optimization: `{"status": str, "objective": float, "variables": {...}, "shadow_prices": {...}}`.

## Category 4 — Geospatial & Mapping

### Overview

geopandas and folium give Noodle a geospatial capability that n8n has zero of. Business users can geocode addresses, spatial-join datasets by geography, and generate interactive maps as workflow artifacts.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `map_generate` ★ | Folium interactive HTML map artifact — markers, choropleth, or heatmap from DatasetRef | `folium`, `pandas` |
| `geocode` | Address string → lat/lng; bulk geocoding of a DatasetRef address column | `geopy` |
| `reverse_geocode` | lat/lng → address string | `geopy` |
| `shapefile_read` | Read shapefile/GeoJSON/GeoParquet to DatasetRef with geometry column | `geopandas` |
| `geospatial_join` | Spatial join of two DatasetRefs (e.g. point-in-polygon, nearest) | `geopandas` |
| `geospatial_buffer` | Buffer zones (radius in metres) around points/lines/polygons | `geopandas` |
| `geospatial_distance` | Distance matrix or nearest-neighbour between two point DatasetRefs | `geopandas`, `scipy` |
| `coordinate_transform` | CRS reprojection — WGS84 ↔ any EPSG code | `pyproj` |
| `isochrone_generate` | Travel-time polygon from an origin point using OSM routing | `osmnx` |

### Notes

- `map_generate` output is an HTML artifact; the editor renders it in an artifact preview iframe.
- Geocoding nodes should support configurable providers (Nominatim, Google, HERE) through a credential param.

## Category 5 — Audio & Media Processing

### Overview

Python is uniquely capable for audio, image, and video work. The headline node is `whisper_transcribe_local` — offline transcription with no API key, which is a data-sovereignty story no SaaS automation platform can tell.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `whisper_transcribe_local` ★ | Transcribe audio or video artifact to text + word-level timestamps; runs fully on host, no API | `faster-whisper` |
| `audio_analyze` | BPM, pitch, spectral features, beat tracking from audio artifact | `librosa` |
| `audio_convert_trim` | Format convert, trim by time range, fade in/out, volume adjust | `pydub` |
| `audio_merge_split` | Concatenate audio files or split on silence into segments | `pydub` |
| `tts_local` | Text-to-speech locally; returns audio artifact; no provider API required | `kokoro` or `coqui-tts` |
| `image_transform` | Resize, crop, rotate, watermark, format convert image artifact | `pillow` |
| `image_analyze_cv` | Edge detection, colour histogram, face detection, template matching | `opencv-python` |
| `image_ocr` | OCR a photo or screenshot to text (scene text, not structured document) | `pytesseract` or `easyocr` |
| `image_metadata` | Read or strip EXIF metadata from image artifact | `pillow`, `piexif` |
| `image_composite` | Layer images, add text overlays, generate social-card-style images | `pillow` |
| `video_extract_frames` | Extract frames at a configured interval to image artifact list | `opencv-python` or `moviepy` |
| `video_clip_trim` | Trim clip to time range, concatenate segments, add audio track | `moviepy` |
| `video_extract_audio` | Rip audio track from video artifact | `moviepy` |
| `video_to_gif` | Render a short video clip to an animated GIF artifact | `moviepy` |
| `video_metadata` | Duration, resolution, codec, FPS from video artifact | `moviepy` |

### Notes

- `whisper_transcribe_local` should support model size param (`tiny`, `base`, `small`, `medium`, `large-v3`) so users can trade accuracy for speed/VRAM.
- All media nodes must accept artifact refs as input and return artifact refs as output — never inline binary.
- `tts_local` and `whisper_transcribe_local` should carry a data-sovereignty note in their node description: "Audio never leaves your host."

## Category 6 — Scientific & Domain Verticals

### Overview

These nodes target specific professional domains. They are not for every Noodle user — they are for the data scientist, quant, bioinformatician, or chemist who reaches for n8n, finds it can't run their library, and has to write a Windmill script instead. Noodle gives them a visual node.

### Finance

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `financial_data_fetch` | OHLCV prices, fundamentals, dividends → DatasetRef via yfinance | `yfinance`, `pandas` |
| `technical_indicator` | RSI, MACD, Bollinger Bands, SMA/EMA computed on OHLCV DatasetRef | `pandas-ta` |
| `portfolio_optimize` | Mean-variance or CVaR portfolio weights from returns DatasetRef | `cvxpy`, `numpy` |
| `options_price` | Black-Scholes call/put pricing and Greeks | `mibian` |
| `fx_convert` | Historical and live FX rate conversion for a DatasetRef amount column | `forex-python` |

### Bioinformatics

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `sequence_parse` | Read FASTA/FASTQ/GenBank artifact to sequence records | `biopython` |
| `sequence_align` | Pairwise or multiple sequence alignment, return alignment artifact | `biopython` |
| `blast_search` | Submit BLAST query to NCBI, return hit records as DatasetRef | `biopython` |

### Chemistry

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `molecule_parse` | Parse SMILES/InChI string to molecule metadata | `rdkit` |
| `molecular_fingerprint` | Compute Morgan/ECFP fingerprints from SMILES column | `rdkit` |
| `molecule_similarity` | Tanimoto similarity matrix from a SMILES DatasetRef | `rdkit` |
| `molecule_visualize` | 2D structure image artifact from SMILES | `rdkit` |

### Graph / Network Analysis

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `graph_analyze` | Degree/betweenness/closeness centrality, clustering coefficient, shortest path, community detection from edge-list DatasetRef | `networkx` |
| `graph_visualize` | Force-directed layout image artifact from edge-list DatasetRef | `networkx`, `matplotlib` |

### Symbolic & Scientific Computing

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `symbolic_math` | Solve equations, differentiate, integrate, simplify symbolically; output LaTeX string + numeric result | `sympy` |
| `unit_convert` | Unit conversion with full dimensional analysis (metres ↔ feet, kg ↔ lbs, joules ↔ calories, etc.) | `pint` |

## Category 7 — Data Quality & Validation

### Overview

Data quality is an unglamorous but high-value workflow use case. These nodes plug into the DatasetRef data plane: validate schemas, profile distributions, fuzzy-match records, and detect outliers before data reaches a production system or LLM fine-tuning pipeline.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `data_profile_report` ★ | Full HTML profiling report artifact from a DatasetRef — distributions, correlations, missing values, duplicates | `ydata-profiling` |
| `schema_validate` | Pandera schema validation on a DatasetRef — pass/fail branch + error DatasetRef on failures | `pandera` |
| `expectation_suite_run` | Run a great_expectations suite against a DatasetRef; output validation result + report artifact | `great-expectations` |
| `record_linkage` | Fuzzy match and deduplicate records across two DatasetRefs using configurable blocking and comparison rules | `recordlinkage` or `dedupe` |
| `data_reconcile` | Compare two DatasetRefs: missing rows, changed values, schema drift; output diff DatasetRef | `pandas` |
| `outlier_detect_statistical` | Flag outliers using IQR, z-score, or modified z-score; output DatasetRef with `_outlier` flag column | `scipy`, `pandas` |
| `string_normalize` | Fuzzy string standardization and phonetic matching on a text column | `rapidfuzz`, `unidecode` |
| `date_parse_normalize` | Parse ambiguous date strings, normalize to ISO 8601 with timezone | `dateparser` |
| `currency_normalize` | Parse "$1,234.56" / "€1.234,56" → Decimal value + currency code column | `babel` |

### Output contracts

- `schema_validate` outputs: `pass` branch on success, `fail` branch with DatasetRef of rows that failed validation.
- `data_profile_report` outputs: HTML ArtifactRef + summary dict `{"rows": int, "columns": int, "missing_cells": int, "duplicate_rows": int}`.

## Category 8 — Cryptography & Security Automation

### Overview

These nodes support security automation workflows: check certificate expiry, encrypt/decrypt files, verify JWT tokens, query Active Directory, and transfer files over SFTP. They require careful redaction — credentials and key material must never appear in node run outputs.

### Nodes

| Node ID | Purpose | Requirements |
|---------|---------|-------------|
| `certificate_inspect` | Parse X.509 certificate: expiry date, SANs, issuer, chain validity | `cryptography` |
| `rsa_sign_verify` | RSA sign or verify data; encrypt or decrypt with a key artifact | `cryptography` |
| `pgp_encrypt_decrypt` | PGP sign/verify/encrypt/decrypt using a key artifact | `python-gnupg` |
| `jwt_sign_verify` | JWT encode, decode, and verify with HMAC or RSA key | `pyjwt` |
| `ldap_query` | Query an LDAP or Active Directory server; return user/group records | `ldap3` |
| `sftp_transfer` | SFTP upload, download, or list files using a credential (distinct from SSH Execute Command) | `paramiko` |
| `password_strength_check` | Check password entropy, common-password list, and policy compliance | `passlib` |
| `network_port_probe` | Check if a set of host:port pairs are reachable; return status records | stdlib `socket` |
| `file_integrity_manifest` | Generate a SHA-256 manifest for a set of artifact refs; output manifest DatasetRef | stdlib `hashlib` |

### Security requirements

- Key material and passphrases must be sourced from Noodle credentials, not plain config params.
- Node outputs must never include raw key bytes, decrypted file contents, or LDAP bind passwords.
- `ldap_query` must be marked `tool_side_effecting=True` for writes; read-only queries may be `False`.
- `sftp_transfer` must be marked `tool_side_effecting=True`.

## Environment Presets

These are template presets, not default installs. Users pick a preset when creating an environment.

| Preset Name | Packages |
|-------------|---------|
| Document Processing | `pdfplumber`, `camelot-py`, `pymupdf`, `pypdf`, `weasyprint`, `python-docx`, `openpyxl`, `python-pptx`, `pytesseract`, `docling`, `qrcode`, `pyzbar` |
| Browser Automation | `playwright` (+ `playwright install chromium` post-install step) |
| Data Science | `scipy`, `statsmodels`, `prophet`, `pmdarima`, `lifelines`, `pulp`, `SALib`, `umap-learn` |
| Geospatial | `geopandas`, `folium`, `geopy`, `pyproj`, `osmnx` |
| Media Processing | `faster-whisper`, `librosa`, `pydub`, `pillow`, `opencv-python`, `pytesseract`, `moviepy` |
| Finance & Quant | `yfinance`, `pandas-ta`, `cvxpy`, `mibian`, `forex-python` |
| Data Quality | `pandera`, `great-expectations`, `ydata-profiling`, `recordlinkage`, `rapidfuzz`, `dateparser`, `babel` |
| Security Automation | `cryptography`, `python-gnupg`, `pyjwt`, `ldap3`, `paramiko`, `passlib` |

## Testing Strategy

### Import safety

Every module must be importable without any of its optional packages installed:

```python
import noodle_nodes.document_intelligence  # must not import pdfplumber at module scope
import noodle_nodes.browser_automation     # must not import playwright at module scope
# etc.
```

Add a test in `test_node_requirements.py` that imports all eight new modules in a bare environment.

### Unit tests per module

- Requirement metadata present on every node (`requirements` field non-empty for nodes with optional deps).
- All heavy packages mocked; node logic tested with mock return values.
- Output shape validated: artifact refs have correct `content_type`; DatasetRefs pass `is_dataset_ref()`.
- Error path: missing package raises `RuntimeError` with actionable message.
- Error path: malformed input (bad PDF bytes, unreachable URL, unsolvable LP) raises descriptive error.

### Integration tests (slow, skipped by default)

- One real round-trip per killer node against a local fixture file.
- Marked `@pytest.mark.slow` and excluded from CI default run.

## Rollout Order

Ship in this order to deliver value incrementally without blocking on heavy dependencies.

### Wave 1 — Document Intelligence MVP

No browser, no GPU, no heavy scientific libs. Ships fast.

1. `pdf_extract_text`
2. `pdf_extract_tables` ★
3. `pdf_generate` ★
4. `docx_generate` ★
5. `excel_report_generate` ★
6. `docx_extract`
7. `excel_extract`
8. `barcode_qr_generate`
9. `barcode_qr_decode`

### Wave 2 — Browser Automation

1. `browser_screenshot` ★
2. `browser_scrape` ★
3. `html_extract`
4. `web_feed_parse`
5. `graphql_request`
6. `browser_click_fill`
7. `browser_pdf_from_url`

### Wave 3 — Statistical Analysis + Data Profile

`data_profile_report` is pulled forward from Category 7 because it is a killer node with a single lightweight dependency and no GPU/browser requirement.

1. `statistical_test` ★
2. `time_series_forecast` ★
3. `optimization_solve` ★
4. `data_profile_report` ★ (Category 7 killer node — shipped early)
5. `correlation_analysis`
6. `regression_analysis`
7. `time_series_decompose`
8. `embedding_visualize` ★

### Wave 4 — Media Processing

1. `whisper_transcribe_local` ★
2. `image_transform`
3. `image_ocr`
4. `audio_convert_trim`
5. `video_extract_frames`
6. `video_extract_audio`

### Wave 5 — Geospatial, Data Quality, Security

1. `map_generate` ★
2. `geocode`
3. `schema_validate`
4. `record_linkage`
5. `certificate_inspect`
6. `sftp_transfer`
7. `jwt_sign_verify`

### Wave 6 — Domain Verticals

Finance, bioinformatics, chemistry, graph analysis, symbolic math — ship after core waves are stable.

## Open Questions

- Should `browser_auth_session` store session state as a Noodle credential or a short-lived artifact? Credentials have scopes and audit; artifacts have retention. Leaning toward credential with a short TTL.
- `playwright install chromium` is a post-install step. Should the environment setup UI surface custom post-install commands, or should this be handled by a special `playwright` preset hook?
- `whisper_transcribe_local` on CPU is slow for long files. Should there be a `max_duration_seconds` guard that warns the user and recommends a GPU runner pool?
- For `tts_local` — Kokoro and Coqui TTS have different model download behaviours. Should model weights be downloaded into the environment artifact cache, or left to the package's default download path?
- Domain vertical nodes (bioinformatics, chemistry) have small audiences. Should they ship as optional community-style modules rather than built-in nodes, to keep the default palette uncluttered?
