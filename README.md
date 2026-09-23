# SmartAnalytica — Content Detection and Analysis

A web application that finds plagiarism in student assignments. It compares every
submission for an assignment against every other one — and against everything
submitted previously — then produces a report showing not just a similarity
percentage, but **the exact passages that matched and who they matched**.

> Final Year Project · BS(Hons) Computer Science · Session 2020–2024
> Department of Computer Science, GC University Lahore

📄 **[Full project documentation](SmartAnalytica-Documentation.pdf)** — the
complete dissertation: problem statement, literature review, requirements,
system design, implementation and evaluation.

---

## Table of contents

1. [What it does](#what-it-does)
2. [How the detection works](#how-the-detection-works)
3. [Quick start](#quick-start) ← **start here**
4. [Your first run](#your-first-run)
5. [Running the tests](#running-the-tests)
6. [Troubleshooting](#troubleshooting)
7. [Configuration](#configuration)
8. [Project structure](#project-structure)
9. [Limits and known gaps](#limits-and-known-gaps)
10. [Future work](#future-work)

---

## What it does

Three kinds of user, each with their own dashboard.

| Role | What they can do |
|---|---|
| **Student** | See enrolled courses and assignments, upload or replace a submission, view their own similarity result and marks |
| **Teacher** | Create courses and assignments, enrol students, run plagiarism detection with a chosen algorithm, read reports, compare any flagged pair side by side, monitor integrity across their courses |
| **Administrator** | Institution-wide usage metrics, full activity log with filtering, integrity monitoring with repeat-offender tracking |

A finished report gives a teacher, for every student:

- a **similarity index** (0–100%) and a severity band
- the **verbatim passages** that appear in someone else's submission
- **paraphrased sentences** detected by meaning rather than wording
- a warning if the work was **already submitted for a different assignment**
- a **suggested mark**, with the deduction shown separately

---

## How the detection works

Seven steps, from upload to marks:

| Step | What happens |
|---|---|
| 1. Submission | Student uploads a file |
| 2. Preprocessing | Unicode normalized, formatting and code comments stripped, text tokenized |
| 3. Initial check | Exact-duplicate scan, including against everything submitted previously |
| 4. Advanced analysis | Lexical + structural + semantic comparison |
| 5. Similarity index | Each submission scored by its strongest match |
| 6. Report | Matched passages located and attributed |
| 7. Marks | Severity banding and proportional deduction |

### Three kinds of analysis, and why all three are needed

**Lexical** — five classical algorithms, all hand-implemented:

| Algorithm | Finds |
|---|---|
| Jaccard | Shared vocabulary, ignoring order and repetition |
| Longest Common Subsequence | Shared word order; survives padding and insertions |
| Levenshtein | Edit distance; catches small disguising edits |
| Cosine | Term-frequency angle, IDF-weighted so shared boilerplate counts for less |
| Rabin–Karp | Verbatim copied passages, via rolling hashes over 5-word windows |

**Structural** — the MOSS/JPlag approach for code. Python is parsed with `ast`
into a structure-only token stream where identifiers and literals become their
*kind*; other languages get an equivalent normalizer. Both streams are
fingerprinted with winnowing (Schleimer, Wilkerson & Aiken, 2003). **Renaming
every variable does not change the result.**

**Semantic** — sentence embeddings compare *meaning*, which is what catches
paraphrasing. Three backends are tried in order so the system degrades instead
of failing: `sentence-transformers` → scikit-learn LSA → a pure-Python n-gram
model. Every report footer names the backend actually used.

### Why it needs all three — the built-in demo proves it

Run `python manage.py seed_demo`, then run detection. You get:

| Submission | Score | What caught it |
|---|---|---|
| Verbatim copy | 100% | Step 3 exact match |
| **Code with every variable renamed** | **75%** | Structural = 100%, Rabin–Karp = **0%** |
| Partial copy | 48.5% | Rabin–Karp = 45% |
| **Paraphrase, no shared wording** | **47.7%** | Semantic = 63.6%, Rabin–Karp = **0%** |
| Recycled from a past semester | flagged | Step 3 archive check |
| Genuinely original work | 16.5% | Not flagged |

The two bold rows are invisible to word-matching alone. That is the whole
argument for the structural and semantic layers.

---

## Quick start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.9 or newer** | Check with `python3 --version`. Developed and tested on 3.9; Django 4.2 supports up to Python 3.12 |
| **~1 GB free disk** | Mostly the optional semantic model |
| No database server | Uses SQLite, which ships with Python |
| Internet, first run only | To download packages, and the embedding model if you enable it |

---

### Option A — one command (recommended)

From inside the `SmartAnalytica` folder:

```bash
python3 install.py          # macOS / Linux
python install.py           # Windows
```

That script creates the virtual environment, installs everything, generates a
`SECRET_KEY`, prepares the database, and **loads a full demo including a
finished plagiarism report**. It takes a few minutes, mostly downloading.

To skip the large semantic model (see the trade-off table below):

```bash
python3 install.py --no-semantic
```

When it finishes it prints exactly how to start the server. Jump to
[Your first run](#your-first-run).

---

### Option B — manual setup

Do this if you would rather see each step, or if `install.py` fails.

**1. Create a virtual environment**

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Windows (Command Prompt):
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

Your prompt should now start with `(.venv)`. Re-run the activate line in every
new terminal.

**2. Install dependencies**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

**3. Create your settings file**

The app refuses to start without a secret key — deliberately, so no deployment
ever runs on a key committed to a repository.

macOS / Linux:
```bash
cp .env.example plagiarism_detector/.env
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Windows (PowerShell):
```powershell
Copy-Item .env.example plagiarism_detector\.env
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Paste the printed key into `plagiarism_detector/.env` so it reads:

```ini
SECRET_KEY=<the key you just generated>
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
```

**4. Set up the database and demo data**

```bash
cd plagiarism_detector
python manage.py quickstart
```

`quickstart` runs the migrations, loads the demo cohort and produces a finished
report. To rebuild the demo from scratch later, add `--reset`.

**5. (Recommended) Enable paraphrase detection**

```bash
pip install sentence-transformers
```

This pulls in PyTorch — about **320 MB** — so it is kept out of
`requirements.txt` to keep the basic install small.

**You can skip it, but you lose real capability.** Measured on the demo cohort:

| | With `sentence-transformers` | Without (scikit-learn fallback) |
|---|---|---|
| Verbatim copy | 100% ✅ | 100% ✅ |
| Renamed-variable code copy | 75% ✅ | 75% ✅ |
| Recycled from a past semester | flagged ✅ | flagged ✅ |
| **Paraphrase, no shared wording** | **47.7% — flagged ✅** | **39.1% — missed ❌** |

The fallback still detects copying, but it is weakest at the one thing semantic
analysis exists for, and it is *slower*, because it refits a TF-IDF model per
pair instead of reusing a loaded neural model. Every report footer names the
backend that produced it, so you can always tell which mode was used.

**6. Run it**

```bash
python manage.py runserver
```

Open **http://127.0.0.1:8000**. Press `Ctrl+C` to stop.

---

### About the demo data

The demo is **generated from code, not shipped as a database file**. That means
it rebuilds identically on any machine, any operating system, any SQLite
version — and the repository stays free of a binary database and a pile of
uploaded files.

`python manage.py quickstart` (or `install.py`) creates:

- a teacher, an administrator and eight students
- a course and two assignments, one of them from a previous semester
- seven submissions covering every detection case
- **a completed plagiarism report**, so the Reports page has real results the
  moment you sign in
- an activity log covering the whole semester

Rebuild it at any time with `python manage.py seed_demo --reset`.

#### The demo timeline

Everything is dated to the **spring 2024 semester**, so the data reads as a
real term rather than as something generated today:

| | |
|---|---|
| Course runs | 5 Feb – 28 Jun 2024 |
| Previous semester's assignment | due 15 Dec 2023 |
| Assignment under analysis | due 14 Jun 2024, 23:59 |
| Submissions arrive | 10 – 14 Jun 2024, staggered |
| Teacher runs detection | 15 Jun 2024, 10:30 |

To move the demo to different dates, edit the anchor constants at the top of
`academic/management/commands/seed_demo.py` and re-run
`python manage.py seed_demo --reset`.

Note that `submission_date`, `created_at` and `timestamp` are `auto_now_add`
fields, so the seeder sets them with a follow-up `.update()`, which bypasses
`auto_now_add`. If you add new dated demo records, do the same.

#### Before a demonstration

Browsing the app writes new log entries with today's date — that is the audit
trail working correctly. For a clean, wholly 2024 activity log, re-run
`python manage.py seed_demo --reset` just before you present.

## Your first run

After `seed_demo`, sign in with any of these:

| Account | Password | Role |
|---|---|---|
| `demo_teacher` | `Demo!12345` | Teacher |
| `demo_admin` | `Demo!12345` | Administrator |
| `demo_ayesha` | `Demo!12345` | Student |

### See the results immediately

1. Sign in as **`demo_teacher`**
2. Click **Reports** in the top navigation
3. **A finished report is already there** — open it

You will see the seven students ranked by similarity, severity badges,
suggested marks, and a **recycled work** badge on the student whose text was
already submitted in a previous semester.

4. Click **Side by side** on any flagged pair to see the matched passages and
   the per-algorithm breakdown.

The two rows worth studying:

- **zara vs omar — 75%.** Structural analysis says 100%, Rabin–Karp says 0%.
  Every variable was renamed; word matching sees nothing.
- **junaid — 47.7%.** Semantic says 63.6%, Rabin–Karp says 0%. A paraphrase
  with no shared wording at all.

### Run detection yourself

To watch it happen rather than read a stored result: **Reports → Run
detection**, pick any algorithm from the dropdown, and compare how each one
scores the same cohort.

### Registering new accounts

Anyone can register as a **student** at `/register/`.

Registering as a **teacher** additionally requires the **teacher access code**,
because a teacher can see every course, every submission and every plagiarism
report. The field appears on the form only when *Teacher* is selected, and it
is checked on the server — hiding or re-enabling it in the browser achieves
nothing, and rejected attempts are written to the activity log.

The default code is **`GCU-TEACHER-2024`**, set in `plagiarism_detector/.env`.
Change it before using this for real, and issue it only to actual staff:

```ini
TEACHER_ACCESS_CODE=your-own-code-here
```

Leaving it empty disables teacher self-registration altogether, so an
unconfigured deployment fails closed rather than open. Administrators can still
create teacher accounts from the Django admin at `/admin/`.

### Try the other roles

- Sign in as **`demo_ayesha`** → *My Courses* → submit a file, then *My Results*
- Sign in as **`demo_admin`** → *Admin* → system totals, activity log, integrity monitor

### Start from scratch instead

If you would rather build your own data:

```bash
python manage.py createsuperuser     # optional: Django admin access at /admin/
python manage.py runserver
```

Then register an account at `/register/`, choosing Teacher or Student. Create a
course, create an assignment, enrol students, and have them submit.

To make an account an administrator, open `/admin/` as a superuser, find its
**User profile**, and tick `is_admin`.

To wipe the demo data and reload it fresh:

```bash
python manage.py seed_demo --reset
```

---

## Running the tests

```bash
cd plagiarism_detector
python manage.py test academic
```

**92 tests**, roughly 50 seconds. They cover:

- preprocessing, all five classical algorithms, structural analysis, semantic
  thresholds, passage highlighting, text extraction, and the pipeline
- functional tests reproducing **test cases TC1–TC7** from the project documentation
- every documented **use case** from section 3.2.9.2
- the **archive check** against previously submitted work
- regression tests for the access-control defects found in the original build

The suite passes on **both** install paths — with and without
`sentence-transformers`. The semantic tests adapt their expected strength to
whichever backend is active, so a minimal install does not produce false
failures.

Run one group at a time:

```bash
python manage.py test academic.tests.TC7RunPlagiarismDetection   # test case TC7
python manage.py test academic.tests.SemanticTests               # paraphrase thresholds
python manage.py test academic.tests.ArchiveCheckTests           # recycled work
python manage.py test academic.tests.UseCaseTests                # documented use cases
```

---

## Troubleshooting

**`SECRET_KEY` error / `UndefinedValueError` on startup**
You skipped step 4. Make sure `plagiarism_detector/.env` exists and contains a
`SECRET_KEY` line. Note the file lives *inside* the `plagiarism_detector/`
folder, not at the top level.

**`python: command not found`**
Try `python3` instead. On Windows, install Python from python.org and tick
*"Add Python to PATH"* during setup.

**`.venv\Scripts\Activate.ps1 cannot be loaded` (Windows PowerShell)**
Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

**`no such table: academic_course`**
You skipped step 5. Run `python manage.py migrate`.

**Port 8000 already in use**
Run on another port: `python manage.py runserver 8001`

**Detection says "no readable text could be extracted"**
The file has no text layer. Most often a **scanned PDF** — an image of a page,
not text. The app tells you this instead of silently scoring zero. There is no
OCR built in; ask for a text-based file.

**A `.rar` upload fails**
Reading `.rar` needs an external tool. macOS ships `bsdtar`, which handles many
archives. If a particular file fails, install `unar`:
`brew install unar` (macOS) or `sudo apt install unar` (Linux).
`.zip` always works with no extra tools.

**Detection is slow**
Semantic analysis is the expensive part. The embedding model loads once per
server start, so the first run of a session is slower than the rest. The work
also grows quadratically: every submission is compared with every other, so a
class of 30 means 435 comparisons.

To speed it up, set `SEMANTIC_ANALYSIS_ENABLED=False` in your `.env`, or choose
a single algorithm instead of *Combined* when running detection. Lexical and
structural analysis are orders of magnitude faster than semantic.

**I changed `.env` but nothing happened**
Settings are read once when the server starts, and the auto-reloader only
watches `.py` files — not `.env`. Stop the server with `Ctrl+C` and start it
again after any change to `.env`.

**"Teacher registration is currently disabled"**
`TEACHER_ACCESS_CODE` is empty or missing from `plagiarism_detector/.env`. Add
it and restart the server. This is deliberate: an unconfigured deployment
refuses teacher self-registration rather than leaving the role open to anyone.


---

## Configuration

All settings live in `plagiarism_detector/.env`.

| Setting | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(required)* | Django cryptographic key. Generate your own |
| `DEBUG` | `False` | `True` for local development. **Never `True` in production** |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated hostnames |
| `TEACHER_ACCESS_CODE` | `GCU-TEACHER-2024` | **Required to register as a teacher.** Change it before real use. Empty disables teacher self-registration |
| `PLAGIARISM_SIMILARITY_THRESHOLD` | `40.0` | Default flagging threshold; can be overridden per run |
| `MAX_SUBMISSION_SIZE_MB` | `20` | Upload size limit |
| `ALLOWED_SUBMISSION_EXTENSIONS` | see `settings.py` | Accepted upload formats |
| `SEMANTIC_ANALYSIS_ENABLED` | `True` | Set `False` to skip semantic analysis |
| `SEMANTIC_MODEL_NAME` | `all-MiniLM-L6-v2` | Embedding model to use |

With `DEBUG=False`, HTTPS redirect, HSTS, secure cookies and clickjacking
protection switch on automatically. `python manage.py check --deploy` passes
clean.

### Supported upload formats

| Type | Formats |
|---|---|
| Documents | `.pdf` (with text layer), `.docx`, `.txt`, `.md` |
| Source code | `.py`, `.java`, `.c`, `.cpp`, `.js`, `.ts` and more |
| Archives | `.zip`, `.rar` — every readable file inside is extracted |

### How marks are calculated

Nothing is deducted below the flagging threshold. Above it, the deduction scales
with how far past the threshold a submission sits, so a borderline case is not
punished like a verbatim copy:

```
deduction = max_marks × (similarity − threshold) ÷ (100 − threshold)
```

At a 40% threshold and 100 max marks: 40% similarity loses nothing, 70% loses
50 marks, 100% loses everything.

---

## Project structure

```
SmartAnalytica/
├── README.md                        This file
├── install.py                       One-command setup
├── requirements.txt                 Python dependencies
├── .env.example                     Template for your settings file
├── SmartAnalytica-Documentation.pdf The project thesis
├── diagrams/                        UML, use case, sequence and data flow diagrams
├── archive/                         Superseded prototypes (not part of the app)
└── plagiarism_detector/
    ├── manage.py                    Django's command-line entry point
    ├── db.sqlite3                   Created by `migrate`
    ├── academic/                    The application
    │   ├── detection/               The detection engine
    │   │   ├── preprocessing.py       Step 2: clean, tokenize, normalize
    │   │   ├── algorithms.py          The five classical algorithms
    │   │   ├── structural.py          AST + winnowing fingerprints for code
    │   │   ├── semantic.py            Sentence embeddings for paraphrase
    │   │   ├── highlighting.py        Locating the passages that matched
    │   │   └── pipeline.py            Orchestrates all seven steps
    │   ├── management/commands/
    │   │   └── seed_demo.py           Builds the demo cohort
    │   ├── extraction.py            PDF, DOCX, zip, rar and source files
    │   ├── models.py                Database tables
    │   ├── permissions.py           Role-based access control
    │   ├── services.py              Extraction cache, logging, archive check
    │   ├── views.py                 Request handling
    │   ├── forms.py                 Form definitions and validation
    │   ├── urls.py                  URL routing
    │   ├── tests.py                 92 tests
    │   └── templates/academic/      24 HTML pages
    └── plagiarism_detector/         Settings, root URLs, WSGI/ASGI
```

### Where to look first

Screenshots of every screen are in
[`plagiarism_detector/docs/screenshots/`](plagiarism_detector/docs/screenshots/),
with an index explaining what each one shows.

**Start with [`plagiarism_detector/docs/TECHNICAL-WALKTHROUGH.md`](plagiarism_detector/docs/TECHNICAL-WALKTHROUGH.md)** —
it explains every design decision in the detection engine and why it was made
that way, with the measurements behind each one.

| If you want to understand… | Read |
|---|---|
| How a score is produced | `detection/pipeline.py` |
| The five classical algorithms | `detection/algorithms.py` |
| Why renaming variables doesn't work | `detection/structural.py` |
| How paraphrasing is caught | `detection/semantic.py` |
| The data model | `models.py` |
| What each page does | `views.py` |

---

## Limits and known gaps

Stated plainly, so nobody is surprised:

- **This detects copying between students, not AI-generated text.** There is no
  ChatGPT/LLM-output classifier yet — see [Future work](#future-work) below.
- **No external corpus.** Comparison covers submissions inside this system.
  There is no comparison against the web, GitHub or published papers.
- **Scanned PDFs cannot be analysed** — no OCR. The app says so rather than
  scoring zero.
- **LCS and Levenshtein are quadratic** and capped at 4,000 tokens per document.
  A capped comparison is marked as truncated in its result.
- **Single-instance deployment.** SQLite, no load balancing, no message queue.
  Fine for a department; not sized for a whole university.
- **No localization.** English only.
- **A high score is evidence, not a verdict.** The report exists so that a human
  reads the matched passages and decides. Automatic marks are a suggestion.

---

## Future work

Planned extensions, following chapter 5.2 of the
[project documentation](SmartAnalytica-Documentation.pdf):

**AI-generated content detection.** The headline next step. The system
currently finds copying *between students*; it does not judge whether a
submission was written by a language model. The groundwork is already in place
— submissions are extracted, normalized and embedded, so an authorship
classifier would sit alongside the existing analysers rather than replace
anything. The hard part is not the plumbing but the accuracy: a false
accusation of AI use is far more damaging than a missed one, which is why this
ships as future work rather than as a half-reliable feature.

**Enhanced detection algorithms** (5.2.1) — a larger, fine-tuned embedding
model, and structural analysis extended beyond Python's AST to Java and C++
parsers.

**Learning management system integration** (5.2.2) — submission and grade
sync with Moodle, Canvas or Blackboard, so the tool fits an existing workflow
instead of asking staff to adopt another portal.

**Feedback mechanisms** (5.2.3) — turning a similarity score into actionable
guidance: what to cite, how to paraphrase properly, where the student's own
voice is strongest.

**External corpus** — comparison against public code repositories and the web,
not only submissions held in this system.

**Scalability** (5.2.4) — comparisons grow quadratically, so a background task
queue and a server-grade database would be needed before institution-wide use.

---

## Credits

Built as a Final Year Project at GC University Lahore. The structural analysis
follows the winnowing scheme of Schleimer, Wilkerson & Aiken (2003), the basis
of MOSS; the semantic layer uses sentence-transformers embeddings.
