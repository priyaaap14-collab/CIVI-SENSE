# CiviSense – Intelligent Civic Complaint Analysis, Duplicate Detection & Resolution Prioritization

An academic **Data Mining + Data Wrangling** project: Django + PostgreSQL web application with citizen, officer and
administrator roles, built around the real *Bengaluru Civic Complaints* dataset. CiviSense is an independent prototype –
it is **not** integrated with BBMP or any government system.

## Dataset provenance (verified)
* Source: https://github.com/sharmila-udaiyar/bengaluru-civic-complaints-analytics (`data/raw/bengaluru_civic_complaints_raw.csv`, MIT licence – see `data/DATASET_LICENSE.txt`).
* The repository README describes the data as real citizen complaints filed in Bengaluru, Jan 2019 – Jul 2022. The upstream repo does not document the original public source, so original provenance beyond that is **unverified**.
* Verified by the pipeline: **16,071 raw rows**, cp1252 encoding, 17 columns, 8 mixed date-string formats (month-first) → **16,038** after removing 33 exact duplicates.
* Missing fields / limitations: no resolution timestamp, no citizen id, no severity field, `address` often empty (7,163 rows), department missing for 652 rows (kept unassigned, never guessed), 32 rows without category (kept as *Uncategorised*), 28 rows with no Latin-script text after cleaning (kept, excluded from NLP).
* Historical records are labelled `source = HISTORICAL` (refs `H-000001…`); records submitted through the app are `source = LIVE` (refs `CS-2026-000001…`). All mining metrics use historical data only.

## Setup on Windows (PowerShell, Python 3.12 recommended, PostgreSQL 14+)
```powershell
cd CiviSense
python -m venv venv
.\venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt

# PostgreSQL (psql from the PostgreSQL bin folder; you will be asked for the postgres password)
psql -U postgres -c "CREATE USER civisense WITH PASSWORD 'civisense123' CREATEDB;"
psql -U postgres -c "CREATE DATABASE civisense OWNER civisense;"
# Different credentials? set CIVISENSE_DB_NAME / _USER / _PASSWORD / _HOST / _PORT environment variables.

python manage.py migrate
python manage.py wrangle            # raw CSV -> data/cleaned/complaints_clean.csv + wrangling_report.json
python manage.py import_history     # loads 16,038 cleaned records into PostgreSQL
python manage.py train_models       # classifier, duplicate index + evaluation, duplicate pairs, priority scores (~1-2 min)
python manage.py create_demo_users  # local test accounts (password CiviSense@123): admin, citizen1, officer_bbmp, officer_bescom, officer_bwssb, officer_btp
python manage.py runserver
```
Open http://127.0.0.1:8000/. Run `python manage.py test core` to execute the automated tests (needs the `CREATEDB` right granted above).
Use `/django-admin/` (admin account) to create officers/users, departments, wards, categories (incl. category severity weights).
Maps and charts load Leaflet, Plotly, Bootstrap from CDNs, so an internet connection is needed in the browser.

## Structure
```
manage.py  requirements.txt  README.md
config/            settings, root urls
core/              models.py views.py urls.py forms.py admin.py services.py tests.py management/commands/{wrangle,import_history,train_models,create_demo_users}.py
ml/                wrangling.py (Pandas pipeline)  mining.py (4 algorithms + duplicate scoring)  priority.py (rule engine)  artifacts/ (joblib models, results.json)
templates/ static/ data/{raw,cleaned}/ media/
```

## Algorithms (all in `ml/`)
| Feature | Method | Notes |
|---|---|---|
| Classification | TF-IDF (1–2 grams) + Logistic Regression (balanced) | trained on real category labels, saved with Joblib; categories with < 20 records (Covid 19, Fire Safety) excluded from training |
| Duplicate detection | TF-IDF + cosine similarity, scaled by distance (full ≤ 250 m, zero at 1.5 km, 0.85 if location missing) and date proximity | suggestions only – nothing is merged/deleted; officers confirm or reject; confirmed pairs share a `DuplicateGroup` |
| Hotspots | DBSCAN, haversine metric (default 300 m, min 15) | recomputed on demand with category / ward / source filters; invalid coordinates are never invented |
| Ward clustering | K-Means on standardised features (log volume, backlog rate, resolved rate, comments, age of open complaints, category shares) | k chosen by highest silhouette in 3–8; elbow + silhouette charts shown |
| Priority | transparent rule score 0–100 = category weight (25) + severity (25) + related reports (20) + waiting time (20) + public impact (10) | Critical ≥ 65, High ≥ 50, Medium ≥ 35, else Low; officers can override with a mandatory reason |

## Actual results from the last run (also shown live on the *Data mining* page)
* **Classifier** (20% stratified held-out test, 3,192 complaints, 20 classes): accuracy **0.804**, macro precision 0.627, macro recall 0.707, macro F1 **0.655**. Frequent categories score much better than rare ones; the per-class table and confusion matrix are in the app.
* **Duplicate detection**: evaluated on a *constructed* set (synthetic re-worded copies vs. different real complaints). Precision/recall/F1 at threshold 0.5: ≈ 0.999 / 0.972 / 0.985. These are optimistic because the positives are easy synthetic paraphrases and there are no human duplicate labels. On the real historical data, 2,273 suspected pairs (2,003 complaints) were found at threshold 0.5 for officer review.
* **DBSCAN** (300 m, min 15, 16,038 mapped complaints): 166 clusters, 5,681 noise points, silhouette 0.318, Davies-Bouldin 0.60.
* **K-Means** (190 wards with ≥ 10 complaints, 8 excluded): chosen k = 7, silhouette **0.128** – a low value, i.e. ward groups overlap and should be read as soft tendencies, not sharp segments.
* Wrangling: 16,071 → 16,038 records, 33 exact duplicates removed (logged in `data/cleaned/removed_exact_duplicates.csv`), 43 → 22 categories (+ Uncategorised), 198 wards, 16,038 valid coordinates (no invalid ones in this data).

## Testing performed
Automated tests (`python manage.py test core`, 16 tests, all passing) cover registration, role-specific login, role-based access, complaint submission, classification of new complaints, image upload validation, duplicate detection and confirmation, assignment, status updates with history and notifications, priority override, category correction, DBSCAN API, dashboard KPIs and filters. All pages were also rendered with the full 16,038-record database. The pages were tested through Django's test client – **layout, Leaflet maps and Plotly charts were not visually inspected in a browser**, so please click through every page once.

## Notes and limits
* Priority is a triage aid, not a prediction of danger or of resolution. Historical records have no severity, so a neutral midpoint is used, and public comment count acts as the impact proxy; waiting time for historical records is measured to the dataset end date (2022-07-31). As a result many old open historical complaints score High.
* Corrected categories on live complaints are stored and added to the training data the next time `train_models` runs.
* Citizen details are hidden from officers; images get random file names and are validated (type, size, Pillow check). Set `CIVISENSE_DEBUG=0`, a real `CIVISENSE_SECRET_KEY` and proper hosts before any real deployment.

---

# Track 1 – "Make MCP Matter": MCP server, Claude assistant, voice complaints, corrected maps

## What was added (nothing was removed)
| Area | Where |
|---|---|
| MCP server (official Python SDK), 10 tools | `civisense_mcp/` (`server.py` tools + validation, `backend.py` real Django services, `security.py` tokens) |
| Claude agent (discovers + calls the tools over MCP) | `core/assistant.py`, `core/views_assistant.py`, `templates/assistant.html`, `static/js/assistant.js` |
| Bilingual voice / typed complaint review | `static/js/voice.js`, `static/js/submit.js`, `core/kannada.py`, `core/extract.py`, `/api/complaint/analyze/` |
| Corrected maps | `core/geo_utils.py`, `core/views.py` (`api_geo`, `api_geo_point`), `static/js/geo.js` |
| Central typography | `static/css/civisense.css` (`--cs-font`: Inter, Noto Sans Kannada) |

The ten tools: `submit_complaint`, `check_complaint_status`, `find_duplicate_complaints`, `classify_complaint`, `calculate_priority`, `get_nearby_complaints`, `get_civic_hotspots`, `get_ward_analytics`, `get_officer_tasks`, `update_complaint_status`. They call the same services, forms, ML models and DBSCAN code as the website – there are no canned chatbot replies and no parallel re-implementation.

## Setup on Windows PowerShell (in addition to the steps above)
```powershell
pip install -r requirements.txt            # adds mcp, anthropic, python-dotenv
Copy-Item .env.example .env                # then edit .env
python manage.py migrate                   # applies 0002 (complaint language + input method)
```
Generate secrets:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"     # use for CIVISENSE_SECRET_KEY and CIVISENSE_MCP_SECRET
```

## Claude API setup
1. Create a key in the Anthropic Console. 2. Put it in `.env` as `ANTHROPIC_API_KEY=...` (or `$env:ANTHROPIC_API_KEY="..."` for the current PowerShell window). 3. Optionally set `CIVISENSE_CLAUDE_MODEL` (default `claude-sonnet-5-5`). 4. Restart `runserver`, sign in, open **AI assistant**.
The key is read only in `config/settings.py`, is never rendered into a template or JavaScript, and is **not** passed to the MCP subprocess. Without a key the assistant page says it is not configured; everything else keeps working.

## Starting the MCP server
The web assistant starts the server itself (stdio subprocess) for each request, with a short-lived signed identity token for the logged-in user. To run it by hand:
```powershell
python manage.py mcp_token --user officer_bbmp          # prints an identity token (default 1 h)
$env:CIVISENSE_MCP_TOKEN = "<token>"
python -m civisense_mcp                                  # stdio (what Claude Desktop / MCP Inspector launch)
python -m civisense_mcp --transport streamable-http --port 8765   # http://127.0.0.1:8765/mcp, needs  Authorization: Bearer <token>
```
MCP Inspector: `npx @modelcontextprotocol/inspector python -m civisense_mcp` with `CIVISENSE_MCP_TOKEN` set.

## Security design
* **Identity is never supplied by the model or the browser.** Django's session user is turned into an HMAC-signed, expiring token; the server re-verifies it on every call and takes role/department from the database, not from the token body or tool arguments.
* **Permissions are enforced in the backend** (role matrix per tool; citizens see only their own complaints; officers only their department's data; another user's complaint returns the same "not found" as a missing one). Claude cannot widen its own access.
* **Confirmation before sensitive actions** (`submit_complaint`, `update_complaint_status`): the server returns a preview and does nothing; the assistant UI shows a Confirm/Cancel card; only a click mints an approval token bound to that user, that tool, those exact arguments, valid 10 minutes. A model-supplied approval token is discarded.
* **Audit log**: every tool call (allowed, refused, failed, confirmation-required) is written to the existing audit table, as are assistant messages and confirm/decline clicks.
* Input validation (types, lengths, Bengaluru coordinate box, reference format), per-user rate limits, no stack traces or internals returned to the model, citizen personal data redacted for officers.
* `CIVISENSE_DEBUG=0` refuses to start with the default secret key.

## Voice complaints – setup and limitations
* Uses the browser's **Web Speech API**. Use **Chrome or Edge**; Firefox has no SpeechRecognition. Needs internet (audio goes to the browser vendor's speech service) and `https://` or `http://localhost`. Allow microphone access when asked.
* Choose English (`en-IN`) or Kannada (`kn-IN`). **Kannada recognition depends on the browser/OS/device and was NOT tested with real speech in this project** – do not assume it works on your device. If nothing is recognised the page says so and the typed box stays available.
* The recognised text appears in an editable box and is **never submitted automatically**: the citizen reviews it, ticks "I have checked this text", and presses Submit. The original text (Kannada stays Kannada) is stored unchanged with its language and input method (voice/typed).
* Classification and duplicate detection use English-trained models. For Kannada, a **small hand-written keyword list** (`core/kannada.py`) produces English hints for the models only; it covers common civic words, not the language. Kannada suggestions are marked as such and must be checked.
* Ward is never guessed silently. It comes from a ward name found in the text or from the map pin (nearest ward centre estimate, labelled as an estimate) and is confirmed by the citizen.

## Maps – what is shown and how
* Every marker is a stored complaint coordinate. Nothing is invented or jittered. Complaints at the same coordinate (the data has stacks of up to 83) are shown as **one numbered marker** whose popup lists them.
* Bengaluru bounds (lat 12.70–13.30, lon 77.30–77.90). Records outside are counted and reported, not drawn; records without coordinates are counted separately.
* Clusters are the **real DBSCAN** result (haversine, radius and min-points from the filters). Noise points are drawn as plain markers.
* Pins ≥ 6 km from the usual area of their ward are flagged "check location" but stay where they were stored.
* **No ward boundary polygons are drawn** because the dataset has none; the page says so. The "nearest ward" estimate uses the median coordinate of each ward's complaints and is not a boundary.
* Officers see only their department's complaints; other citizens' live reports are anonymised. If map tiles fail to load (CDN/internet), a notice is shown and markers still render.

## Testing performed for Track 1
**Run in the development sandbox and passing (31 tests):**
* `tests/test_mcp_protocol.py` (16) – real MCP client ↔ server session over the SDK: tool discovery, schemas, validation, confirmation gate, forged/expired/mismatched approval tokens, identity binding, audit entries, no error leakage (uses an in-memory fake backend, so it checks the protocol and security layer, not the database).
* `tests/test_assistant_agent.py` (9) – the agent loop with a **scripted stand-in for Claude**: pause for confirmation, confirm, decline, parallel tool calls, ignoring a model-supplied approval token, round limit.
* `tests/test_geo_real_data.py` (6) – the map helpers on the real 16,071-row CSV: all complaints accounted for, markers at stored coordinates, stacks grouped (16,071 → 12,902 markers), real DBSCAN labels, far-flags without moving points. (This test found and fixed a rounding bug that produced two markers at one coordinate.)
* `voice.js` with a **mocked** SpeechRecognition in headless Chromium (8 cases: unsupported, insecure page, mic blocked, no mic, network, silence, empty Kannada, Kannada text preserved). This checks error handling only, not real recognition.
* Syntax/lint: `node --check` on all JS, `ruff`, `py_compile`.

**NOT verified – please check before the demo:**
* `core/test_track1.py` (Django tests for role permissions, citizen privacy, officer department scoping, voice-review enforcement, Kannada text preservation, the ward-pk fix, map scoping, and a missing-migration check) is written but has **never been executed**; expect it may need small fixes on first run.
* The Django test-suite and migration `0002` against PostgreSQL (the sandbox had no Django or database). Run `python manage.py test core` and `python manage.py makemigrations --check --dry-run`.
* Real Claude API calls (no API key in the sandbox) and the stdio MCP subprocess launched from Django against the real database.
* Real English or Kannada speech recognition, Kannada font rendering, and Leaflet/Plotly rendering in a browser.
* Behaviour of the trained ML models (the model files were not in the supplied zip; run `train_models`).

## Manual checklist / demo workflow
1. Log in as `citizen1`. **Submit complaint** → choose Kannada or English → press the microphone (or type) → review/edit the text → check the analysis panel (category, duplicates, priority) → tick the review box → Submit. Confirm the stored description is in the original language.
2. Open **AI assistant** as `citizen1`: "Is there already a complaint about the pothole near Indiranagar?" (watch the tool progress lines, which appear only while a tool really runs) → "Register it for me" → the **Confirm** card shows the exact data → Confirm → reference number returned. Try "Show me another citizen's complaint" and see the refusal.
3. Log in as `officer_bbmp`: "What are my pending tasks?" → "Mark <ref> In progress with note …" → Confirm card → Confirm. Ask about a BWSSB ward statistic and see that only own-department data is returned.
4. Open the Django admin audit log and show the entries for each of the above.
5. **Maps**: check the filters, stacked markers, cluster toggle, the "outside Bengaluru / no coordinates" counts and the ward-boundary note; switch the network off to see the tile notice.
6. MCP Inspector (see above): list the 10 tools and call one with an invalid latitude to show validation.
