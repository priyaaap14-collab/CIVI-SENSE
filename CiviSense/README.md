# CiviSense

### Intelligent Civic Complaint Analysis, Prioritisation & AI-Assisted Resolution

CiviSense is a civic-tech web application designed to make complaint
management more organised, data-driven and transparent. Built around
Bengaluru civic complaint data, it brings together **data wrangling,
machine learning, geospatial analytics, role-based workflows and a
Gemini-powered AI assistant** in one platform.

Citizens can submit and track civic complaints, while department
officers can review assigned complaints, identify similar reports and
manage resolution updates. Administrators can oversee users,
departments, wards and categories. The platform also exposes selected
application capabilities through the **Model Context Protocol (MCP)**,
allowing the AI assistant to interact with the same backend services
used by the web application.

> **Project scope:** CiviSense is an academic prototype and is not an
> official BBMP platform. It is not connected to BBMP or any government
> complaint-management system.

------------------------------------------------------------------------

## Contents

-   [Highlights](#highlights)
-   [How CiviSense works](#how-civisense-works)
-   [Technology stack](#technology-stack)
-   [Machine learning and analytics](#machine-learning-and-analytics)
-   [AI assistant and MCP
    integration](#ai-assistant-and-mcp-integration)
-   [Voice-based complaint
    submission](#voice-based-complaint-submission)
-   [Roles and access](#roles-and-access)
-   [Dataset](#dataset)
-   [Project structure](#project-structure)
-   [Getting started](#getting-started)
-   [Environment configuration](#environment-configuration)
-   [Demo accounts](#demo-accounts)
-   [Running tests](#running-tests)
-   [Important limitations](#important-limitations)
-   [Future scope](#future-scope)

------------------------------------------------------------------------

## Highlights

  -----------------------------------------------------------------------
  Module                              What it does
  ----------------------------------- -----------------------------------
  Complaint management                Supports complaint submission,
                                      assignment, status tracking and
                                      update history

  Intelligent classification          Uses an ML model to suggest a
                                      complaint category from its
                                      description

  Duplicate detection                 Finds potentially similar
                                      complaints to help reduce repeated
                                      manual review

  Priority assessment                 Calculates an explainable priority
                                      score using configurable factors

  Civic analytics                     Presents complaint trends,
                                      ward-level insights and category
                                      distributions

  Geospatial insights                 Displays complaint locations and
                                      DBSCAN-based hotspot clusters on a
                                      map

  Bilingual input                     Supports typed and browser-based
                                      voice input in English and Kannada

  Gemini AI assistant                 Helps users explore complaint
                                      information and initiate supported
                                      workflows through natural language

  MCP integration                     Connects the AI assistant to
                                      validated backend tools through the
                                      Model Context Protocol

  Human approval                      Requires user confirmation before
                                      sensitive actions are carried out
  -----------------------------------------------------------------------

## How CiviSense works

1.  **Report:** A citizen submits a civic issue using typed text or
    voice input.
2.  **Review:** The citizen reviews the recognised or entered
    description and confirms the details.
3.  **Analyse:** CiviSense suggests a category, checks for possible
    duplicates and calculates a priority score.
4.  **Manage:** The complaint becomes available to the appropriate
    workflow based on the user's role and department.
5.  **Track:** Citizens can check complaint status, while officers can
    review and update complaints within their permitted scope.
6.  **Understand:** Dashboards, ward analytics and map-based insights
    help users explore complaint patterns.
7.  **Assist:** The Gemini assistant can use MCP tools to retrieve
    information and prepare supported actions. Sensitive changes remain
    subject to human confirmation.

## Technology stack

  -----------------------------------------------------------------------
  Area                                Technologies
  ----------------------------------- -----------------------------------
  Backend                             Python, Django

  Database                            PostgreSQL

  Data wrangling                      Pandas, NumPy

  Machine learning                    Scikit-learn, TF-IDF, Logistic
                                      Regression, DBSCAN, K-Means

  Model persistence                   Joblib

  AI assistant                        Google Gemini, Google GenAI SDK

  Tool integration                    Model Context Protocol (MCP),
                                      Python MCP SDK

  Data visualisation                  Plotly

  Maps                                Leaflet

  Frontend                            Django Templates, HTML, CSS,
                                      JavaScript, Bootstrap

  Voice input                         Browser Web Speech API

  Image handling                      Pillow
  -----------------------------------------------------------------------

## Machine learning and analytics

CiviSense combines multiple analytical techniques. The models and
analytical outputs are generated from the historical complaint dataset;
live complaints are handled separately.

### 1. Complaint classification

-   Text is converted into numerical features using **TF-IDF (unigrams
    and bigrams)**.
-   A **Logistic Regression** classifier predicts the likely complaint
    category.
-   Class balancing is used during training.
-   The predicted category is a suggestion and can be reviewed or
    corrected.

### 2. Duplicate complaint detection

-   TF-IDF text representations and cosine similarity are used to
    identify semantically similar reports.
-   The matching score can also consider geographic distance and date
    proximity.
-   Results are presented as **potential duplicates**, not automatic
    merges.
-   Officers can review, confirm or reject suggested duplicate
    relationships.

### 3. Complaint prioritisation

A transparent rule-based score from 0 to 100 combines: - Category
weight - Severity - Related reports - Waiting time - Public impact

The score is mapped to Critical, High, Medium or Low priority. Officers
can override a suggested priority with a reason. The score is a triage
aid, not a prediction of danger or resolution time.

### 4. Hotspot detection

-   **DBSCAN** with the Haversine distance metric groups geographically
    close complaints.
-   Users can explore hotspot results using available category, ward and
    source filters.
-   Complaints without valid coordinates are not assigned invented
    locations.

### 5. Ward-level clustering

-   **K-Means** groups wards using standardised complaint and
    service-related indicators.
-   The selected cluster count is evaluated using silhouette analysis.
-   Clusters represent broad patterns and should not be interpreted as
    definitive ward rankings.

### Recorded model and dataset results

The following figures are from the project's documented training and
analysis run. They are dataset-specific and may change when the data or
training configuration changes.

  Evaluation                         Recorded result
  -------------------------------- -----------------
  Raw historical records                      16,071
  Cleaned historical records                  16,038
  Complaint classifier accuracy                0.804
  Classifier macro precision                   0.627
  Classifier macro recall                      0.707
  Classifier macro F1-score                    0.655
  DBSCAN clusters                                166
  K-Means ward clusters selected                   7

**Evaluation note:** Duplicate-detection evaluation used constructed
positive examples, including synthetic reworded copies. Its reported
score should not be interpreted as independently verified real-world
duplicate accuracy. Ward clustering also had a low silhouette score, so
cluster boundaries overlap.

## AI assistant and MCP integration

CiviSense includes a **Google Gemini-powered conversational assistant**.
Instead of directly accessing database records or independently changing
application data, the assistant discovers and invokes tools exposed by
the CiviSense MCP server.

The MCP server delegates operations to the application's existing Django
backend services. This keeps the assistant, website and business rules
connected to the same source of application data.

### MCP capabilities

The current MCP server exposes tools for: - Submitting a complaint -
Checking complaint status - Finding potential duplicate complaints -
Classifying complaint text - Calculating complaint priority - Finding
nearby complaints - Retrieving civic hotspots - Listing wards -
Retrieving ward analytics - Viewing an officer's assigned tasks -
Updating complaint status

### Human-in-the-loop control

Sensitive actions such as complaint submission and status updates use a
confirmation flow:

1.  The assistant prepares the requested action.
2.  The MCP server returns a preview instead of immediately performing
    the change.
3.  The user reviews the details in the application.
4.  The action proceeds only after the user explicitly confirms it.

The MCP layer also uses signed identity tokens, permission checks, input
validation and audit logging. Access is based on the authenticated
application user and their role; the model cannot grant itself
additional permissions.

## Voice-based complaint submission

The complaint form supports typed input and browser-based speech
recognition.

-   English (`en-IN`) and Kannada (`kn-IN`) can be selected where
    supported by the browser and device.
-   Recognised speech is placed in an editable field.
-   The citizen can review and correct the transcript before submission.
-   The original complaint wording is retained; suggested title,
    location and other extracted fields can be reviewed separately.
-   Kannada text is preserved. A small Kannada civic-keyword lexicon can
    provide English hints for the English-trained classification and
    duplicate-matching models.

**Browser note:** Voice input uses the Web Speech API. Chrome or Edge is
recommended. Recognition availability and Kannada accuracy depend on the
browser, operating system and device. Microphone permission and an
internet connection may be required. Typed complaint submission remains
available if speech recognition is unavailable.

## Roles and access

  -----------------------------------------------------------------------
  Role                                Main responsibilities
  ----------------------------------- -----------------------------------
  Citizen                             Register, review and track their
                                      own complaints; use the assistant
                                      for supported citizen workflows

  Department officer                  View and manage complaints
                                      permitted for their department;
                                      review duplicate suggestions and
                                      update complaint status

  Administrator                       Manage users, officers,
                                      departments, wards, categories and
                                      platform configuration
  -----------------------------------------------------------------------

Role-based access is enforced on the backend. The assistant uses the
permissions of the currently authenticated user.

## Dataset

CiviSense uses the Bengaluru Civic Complaints dataset available from:

-   **Dataset repository:** [Bengaluru Civic Complaints
    Analytics](https://github.com/sharmila-udaiyar/bengaluru-civic-complaints-analytics)
-   **Project data file:** `data/raw/bengaluru_civic_complaints_raw.csv`
-   **Licence information:** Refer to `data/DATASET_LICENSE.txt` in the
    project.

The upstream repository describes the records as Bengaluru citizen
complaints from January 2019 to July 2022. The original source from
which the upstream repository obtained the records is not documented
there, so provenance beyond that description is unverified.

The project's documented wrangling run processed **16,071 raw rows** and
produced **16,038 cleaned records**, removing 33 exact duplicates.
Historical records and complaints submitted through the application are
labelled separately (`HISTORICAL` and `LIVE`).

## Project structure

``` text
CiviSense/
├── config/                 # Django project settings and root URLs
├── core/                   # Application models, views, forms and services
│   ├── management/
│   │   └── commands/       # Data, model, demo-user and MCP commands
│   ├── assistant.py        # Gemini assistant and agent workflow
│   ├── views_assistant.py  # Assistant application views
│   ├── kannada.py          # Kannada language helpers
│   └── extract.py          # Complaint text field suggestions
├── civisense_mcp/          # MCP server, backend bridge and security
├── ml/                     # Wrangling, mining and priority logic
│   └── artifacts/          # Saved models and analysis results
├── data/
│   ├── raw/                # Source dataset
│   └── cleaned/            # Cleaned data and wrangling outputs
├── templates/              # Django HTML templates
├── static/
│   ├── css/                # Stylesheets
│   └── js/                 # Frontend and voice functionality
├── media/                  # Uploaded complaint images
├── manage.py
├── requirements.txt
└── README.md
```

## Getting started

### Prerequisites

-   Python 3.12 recommended
-   PostgreSQL 14 or later
-   Git
-   Windows PowerShell instructions are provided below
-   Internet access for CDN-hosted frontend assets and browser speech
    recognition

### 1. Clone the repository

``` bash
git clone https://github.com/priyaaap14-collab/CIVI-SENSE.git
cd CIVI-SENSE
```

If the Django project folder is nested in the repository, change into
the directory that contains `manage.py` before running the following
commands.

### 2. Create and activate a virtual environment

**Windows PowerShell**

``` powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation for the current terminal session:

``` powershell
Set-ExecutionPolicy -Scope Process Bypass
.\venv\Scripts\Activate.ps1
```

**macOS / Linux**

``` bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

``` bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create the PostgreSQL database

Create a PostgreSQL user and database for the project. For example, from
`psql`:

``` sql
CREATE USER civisense WITH PASSWORD 'change-this-password' CREATEDB;
CREATE DATABASE civisense OWNER civisense;
```

Use your own secure password and configure the matching environment
variables in the next step. Do not commit database passwords or secret
keys to Git.

### 5. Configure environment variables

Copy the example environment file:

``` powershell
Copy-Item .env.example .env
```

On macOS / Linux:

``` bash
cp .env.example .env
```

Edit `.env` and provide your local PostgreSQL credentials, a Django
secret key and your Gemini API key. See [Environment
configuration](#environment-configuration).

### 6. Run database migrations

``` bash
python manage.py migrate
```

### 7. Prepare the historical dataset

Run these commands in order:

``` bash
python manage.py wrangle
python manage.py import_history
python manage.py train_models
```

These commands prepare the cleaned dataset, import historical complaints
into PostgreSQL and generate the machine-learning artefacts used by the
application. Training time depends on your system.

### 8. Create demo users (optional)

``` bash
python manage.py create_demo_users
```

See [Demo accounts](#demo-accounts) for the local demonstration
credentials created by this command.

### 9. Start the development server

``` bash
python manage.py runserver
```

Open **http://127.0.0.1:8000/** in your browser.

## Environment configuration

The `.env.example` file lists the supported configuration variables. Set
the values locally in `.env`; do not publish that file or expose API
keys.

  -----------------------------------------------------------------------
  Variable                            Purpose
  ----------------------------------- -----------------------------------
  `CIVISENSE_DEBUG`                   Enables or disables Django debug
                                      mode

  `CIVISENSE_SECRET_KEY`              Django application secret key

  `CIVISENSE_DB_NAME`                 PostgreSQL database name

  `CIVISENSE_DB_USER`                 PostgreSQL username

  `CIVISENSE_DB_PASSWORD`             PostgreSQL password

  `CIVISENSE_DB_HOST`                 PostgreSQL host

  `CIVISENSE_DB_PORT`                 PostgreSQL port

  `GEMINI_API_KEY`                    API key used by the Gemini
                                      assistant

  `CIVISENSE_GEMINI_MODEL`            Gemini model selection

  `CIVISENSE_ASSISTANT_MAX_TOKENS`    Assistant response token limit

  `CIVISENSE_ASSISTANT_RATE`          Assistant rate-limit configuration

  `CIVISENSE_MCP_SECRET`              Secret used for MCP token signing;
                                      the application can fall back to
                                      the Django secret key
  -----------------------------------------------------------------------

Generate a strong Django secret key and a separate MCP secret for local
or production configuration. Keep all secrets out of source control. The
assistant requires a valid Gemini API key; core complaint-management
features can still be explored separately.

## Demo accounts

After running `python manage.py create_demo_users`, the project's
documented local demo accounts are:

  Account            Role / scope
  ------------------ ---------------------------
  `admin`            Administrator
  `citizen1`         Citizen
  `officer_bbmp`     BBMP department officer
  `officer_bescom`   BESCOM department officer
  `officer_bwssb`    BWSSB department officer
  `officer_btp`      BTP department officer

The documented demo password is `CiviSense@123`. These are local
demonstration credentials only. Change or remove them before any
deployment and never reuse them for real accounts.

## Running tests

Run the Django application tests with:

``` bash
python manage.py test core
```

The database user may need permission to create test databases
(`CREATEDB`).

The repository also contains MCP protocol and assistant-agent tests. Run
the relevant test modules using the project's test setup. Some tests use
scripted or fake components to validate protocol behaviour; they do not
replace end-to-end testing with a live Gemini API, a configured
PostgreSQL database and a real browser.

Before a demonstration, manually verify: - Citizen registration, login
and complaint submission - Officer department-level access and status
updates - Admin management pages - Classification, duplicate suggestions
and priority output - Map filters, complaint markers and analytics -
Gemini assistant responses and MCP tool discovery - Confirmation prompts
for sensitive actions - English and Kannada voice input on the target
browser/device

## Important limitations

-   CiviSense is a prototype, not an official government service or a
    live BBMP integration.
-   Historical data does not contain every field needed for operational
    evaluation. For example, it has no resolution timestamp, citizen ID
    or original severity field.
-   Some historical records have missing addresses, departments or
    categories. Missing information is not silently invented.
-   The classifier is trained on historical complaint text and category
    labels. Performance can vary across categories, especially those
    with fewer examples.
-   Kannada classification support is limited by the current
    English-trained models and keyword-based hints. It should not be
    treated as full Kannada-language understanding.
-   Duplicate detection returns suggestions for review; it does not
    automatically merge or delete complaints.
-   Priority is an explainable triage score, not a prediction of danger,
    service-level compliance or resolution time.
-   Map positions depend on available complaint coordinates. Ward
    estimates are not official ward boundary polygons.
-   Browser speech recognition depends on browser/device support and may
    send audio to the browser vendor's speech service.
-   Local development settings are not production settings. A real
    deployment needs secure secrets, `DEBUG` disabled, appropriate
    allowed hosts, HTTPS, hardened database access, backups and
    deployment-specific security review.

## Future scope

Potential areas for further development include: - Improved Kannada
language understanding and evaluation with a representative labelled
dataset - More robust duplicate-detection evaluation using
human-verified complaint pairs - Integration with verified official
civic data sources, subject to authorised access - Ward boundary
visualisation using authoritative geospatial boundary data -
Notifications and service-level monitoring for complaint updates - Model
monitoring, feedback loops and periodic retraining - Accessibility and
multilingual interface improvements - Production deployment with
stronger observability, security and operational controls

------------------------------------------------------------------------

## Acknowledgement

CiviSense was developed as an academic project exploring how data
mining, data wrangling, geospatial analysis and AI-assisted workflows
can support civic complaint management.

**Built with Python, Django, PostgreSQL, Scikit-learn, Google Gemini and
the Model Context Protocol.**
