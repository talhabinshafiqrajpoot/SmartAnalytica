# SmartAnalytica — Technical Walkthrough

How the detection engine works, and **why each decision was made that way**.

Read this to be able to answer follow-up questions about any part of the
system. Every claim below cites the file and line it comes from, so you can
open the code and check it.

---

## 1. The system on one page

A submission travels through seven stages. Thesis section 3.2.3 defines them;
here is which module owns each.

```
   Student uploads a file
            │
   [1] views.submit_assignment          accepts + validates the upload
            │
   [2] extraction.extract_text          PDF / DOCX / zip / rar / source → text
       detection.preprocessing          strip, normalize, tokenize
            │
   [3] pipeline  (exact-match check)    SHA-256 within the assignment
       services.find_archive_matches    + against everything submitted before
            │
   [4] algorithms.py    lexical         5 classical algorithms
       structural.py    program shape   AST + winnowing
       semantic.py      meaning         sentence embeddings
            │
   [5] pipeline._aggregate              → one similarity index per submission
            │
   [6] highlighting.find_matched_passages
       models.Match / PlagiarismReport  → report with passages and sources
            │
   [7] pipeline._build_outcome          severity band + marks
```

The key architectural choice: **`DetectionPipeline` contains no database
code.** It takes plain strings and returns plain objects, so the whole engine
is testable without Django. `run_detection_for_assignment`
(`detection/pipeline.py`) is the only part that touches the ORM.

---

## 2. Module by module

### 2.1 `detection/preprocessing.py` — turning a file into comparable tokens

**What it does.** Strips formatting, normalizes Unicode, tokenizes, and records
the character offset of every token so matched passages can be located later.

**The decision that isn't obvious.** The original prototype did all of this
with `text.lower().split()`.

**Why that was inadequate.** `split()` breaks on whitespace only, so `foo();`
and `foo()` are different tokens, and any change in indentation changes the
token stream. Two identical programs formatted differently scored as partly
different. The current version tokenizes with a regex (`_WORD_RE`) that
discards punctuation, so formatting changes are invisible to the comparison.

It also normalizes smart quotes: text pasted from Word uses `'` and `"`, which
are different characters from the ones a plain editor produces. Without this, a
copy made via Word scores lower than the same copy made in a text editor.

> **Evidence:** `PreprocessingTests.test_smart_quotes_match_plain_quotes` asserts
> that both forms produce identical token lists.

**For code**, comments are stripped (`strip_code_comments`) so that rewriting
comments cannot disguise copied logic.

---

### 2.2 `detection/algorithms.py` — the five classical algorithms

| Algorithm | What it measures | Blind to |
|---|---|---|
| Jaccard | Vocabulary overlap | Word order, repetition |
| LCS | Shared word order | Synonym substitution |
| Levenshtein | Edit distance | Large reorderings |
| Cosine | Term-frequency angle | Word order |
| Rabin-Karp | Verbatim passages | Any paraphrasing |

Each returns an `AlgorithmResult` whose `score` is 0–100, so the pipeline can
treat them interchangeably.

#### Four defects fixed from the original implementation

**(a) LCS allocated the full DP table.** The original built
`(m+1) × (n+1)` Python integers. Two 5,000-word submissions means 25 million
objects. `lcs_similarity` (`algorithms.py:82`) now keeps only two rolling rows,
reducing memory from O(m·n) to O(n).

*Why this is safe:* computing the LCS **length** only needs the previous row.
Reconstructing the actual subsequence would need the full table — the code does
not need it, because matched passages come from `highlighting.py` instead.

**(b) Levenshtein compared raw characters.** A 20,000-character pair cost 400
million cell updates. It now compares **tokens** by default, which is both
faster and more meaningful — one changed word counts as one edit, not five.

**(c) Rabin-Karp searched for single whole words.** The original looped over
every word in document B and re-hashed the whole of document A once per word.
That measures vocabulary overlap — which Jaccard already does — at enormous
cost. It now fingerprints contiguous **5-token windows** in a single pass, which
is the substring-matching use the algorithm is actually for. A score of 30 means
roughly 30% of this submission's 5-word windows also appear in the other.

**(d) `compare_assignments` raised `UnboundLocalError`.** Its `lcs` and
`levenshtein` branches referenced variables only assigned in the `jaccard` and
`cosine` branches. That function is gone; `pipeline._run_lexical` replaces it.

#### Two subtler decisions

**`_stable_token_hash` uses CRC32, not Python's `hash()`** (`algorithms.py:246`).

> `hash()` is salted per interpreter process via `PYTHONHASHSEED`. Fingerprints
> are persisted for the step 3 archive check, so using `hash()` would make every
> stored fingerprint meaningless the moment the server restarts.

> **Evidence:** `AlgorithmTests.test_rabin_karp_hashing_is_deterministic`.

**`MAX_QUADRATIC_TOKENS = 4000`** (`algorithms.py:32`). LCS and Levenshtein are
O(m·n). In a class-wide run of 435 pairs, one oversized pair would stall
everything. Inputs above the cap are truncated and the result is flagged
`truncated=True`, so the report never silently hides that a comparison was
partial.

**IDF weighting** (`compute_idf`, `algorithms.py:227`). Cosine similarity
weights every term equally by default, so the assignment title, standard
imports and the question text — shared by the entire class — inflate every
score equally. IDF computed across the cohort downweights terms that appear in
many submissions, so only *distinctive* shared wording counts.

---

### 2.3 `detection/structural.py` — the hardest part to explain

This is the module that catches renamed code, and the one most likely to be
questioned. Take time over it.

#### The problem

Rename every variable in a program and the text changes completely while the
program stays identical. Your own literature review makes this point about MOSS
and JPlag. Word-matching cannot see it.

> **Evidence:** `StructuralTests.test_lexical_algorithms_are_fooled_by_renaming`
> asserts, as a test, that Rabin-Karp scores *lower* than structural analysis on
> a renamed copy.

#### Step 1 — reduce the program to its shape

Python sources are parsed with the standard-library `ast` module. `_AstTokenizer`
walks the tree and emits **node types**, not names: every `Name` node becomes
the token `NAME`, every constant becomes `CONST_int` and so on.

So this:

```python
def compute_total(values):
    total = 0
    for value in values:
        total += value
```

and this:

```python
def add_up(numbers):
    accumulator = 0
    for number in numbers:
        accumulator += number
```

produce the **same token stream**. Renaming is now invisible.

Other languages fall back to `lexical_tokens`, which keeps keywords and
operators but collapses identifiers to `ID` and numbers to `NUM` — the same
idea without a parser.

If the source does not parse (a syntax error, or a language `ast` cannot read),
the code falls back to the lexical tokenizer rather than failing.

> **Evidence:** `StructuralTests.test_falls_back_when_source_does_not_parse`.

#### Step 2 — winnowing

Now compare two token streams. Hashing every k-gram works but stores far too
many fingerprints. Keeping every *n*-th one is fast but fragile: insert one
token near the start and every subsequent selection shifts, destroying the
match.

**Winnowing** (Schleimer, Wilkerson & Aiken, 2003) solves this. Slide a window
of `w` consecutive k-gram hashes; in each window keep the **minimum**, breaking
ties by taking the rightmost.

Why this survives insertions: the minimum of a window depends on the *values*
in it, not on absolute position. Shift the text and the same hash is still the
minimum of the same group of k-grams, so the same fingerprint is selected.

**The guarantee.** With `DEFAULT_K = 12` and `DEFAULT_WINDOW = 8`
(`structural.py:33-34`), any shared passage of at least

```
k + w − 1  =  12 + 8 − 1  =  19 tokens
```

is guaranteed to contribute at least one shared fingerprint.

*Why:* a 19-token run contains 8 consecutive 12-grams — exactly one full
window — and every window contributes its minimum. So at least one fingerprint
from that run is selected in **both** documents.

**Choosing k and w.** `k` is the noise threshold: matches shorter than 12
tokens are ignored, because short coincidental agreement between two correct
programs is meaningless. `w` trades density against guarantee — larger `w` means
fewer fingerprints stored but a longer minimum detectable match.

**Short files** never fill a window, so `structural_similarity` falls back to
comparing k-grams directly with a smaller `k`.

#### Step 3 — score it

```
score = |shared fingerprints| / min(|A|, |B|) × 100
```

Normalized by the **smaller** set deliberately: a copied file padded with extra
original work should still register as substantially copied. Dividing by the
union would let a student hide a copy by adding filler.

---

### 2.4 `detection/semantic.py` — catching paraphrase

#### Why it exists

"The loop iterates over every record" and "each record is visited by the loop"
share almost no tokens. Every lexical algorithm reports near zero.

> **Evidence:** `SemanticTests.test_lexical_algorithms_miss_what_semantic_catches`.

#### How

Documents are split into sentences of at least 6 words
(`MIN_SENTENCE_WORDS`), embedded, and compared pairwise by cosine similarity.
The score is the share of sentences in the **shorter** document that have a
close counterpart in the other.

*Why framed that way:* a copied paragraph inside a long original essay should
surface as a set of matched passages, not be diluted to nothing by length.

**Model:** `all-MiniLM-L6-v2` — a 6-layer BERT, 22.7M parameters, 384-dimension
embeddings, running on PyTorch.

#### Three backends, tried in order

1. `sentence-transformers` — real contextual embeddings, best quality
2. scikit-learn LSA — TF-IDF reduced by truncated SVD, no download, works offline
3. Pure-Python character n-grams — always available

Chosen once, lazily, on first use — never at import, because that would add
seconds to every `manage.py` command.

#### The calibration — this is your strongest answer

The threshold was **originally set to 0.75 and it detected nothing**, because
real paraphrases do not score that high. Finding and fixing that is worth
describing openly.

Measured at document level, taking the best match per sentence, across three
kinds of document pair:

| Backend | Paraphrase | Same topic, independent | Unrelated |
|---|---|---|---|
| sentence-transformers | 0.59 – 0.85 | ≤ 0.44 | ≤ 0.09 |
| sklearn-lsa | 0.13 – 1.00 | ≤ 0.39 | 0.00 |
| ngram | 0.09 – 0.50 | ≤ 0.17 | ≤ 0.17 |

Thresholds (`semantic.py:56`) sit **above the same-topic ceiling**, not merely
above the unrelated one:

| Backend | Threshold | Result |
|---|---|---|
| sentence-transformers | 0.55 | 5/5 paraphrases caught, 0 false positives |
| sklearn-lsa | 0.40 | 1/5 caught, 0 false positives |
| ngram | 0.22 | 4/5 caught, 0 false positives |

**The reasoning to state out loud:** the expensive error is accusing a student
who merely wrote about the same subject. Two students writing independently
about sorting algorithms score up to 0.44. Setting the cut at 0.55 costs some
recall and buys zero false accusations. On the weaker fallbacks that trade is
harsher — sklearn-lsa catches only 1 in 5 — and that is accepted deliberately,
because they are fallbacks.

Each backend has its own threshold **because they score on different scales**; a
single shared value would be wrong for two of the three.

> **Evidence:** `SemanticTests.test_same_topic_is_not_flagged_as_paraphrase`.

---

### 2.5 `detection/highlighting.py` — the passages behind the number

A percentage alone cannot support an accusation. This module finds the shared
text.

**Seed and extend.** Index every 5-token window of document B
(`SEED_LENGTH = 5`). Walk document A; on a hash hit, extend forward while
tokens keep agreeing, then record the maximal run.

**CRC32 hits are re-verified** before extending — hash collisions are rare but
real, and a false passage in a report is worse than a missing one.

**Consumed positions are skipped**, so one long copied paragraph yields one
passage rather than dozens of overlapping fragments. Runs shorter than
`MIN_PASSAGE_TOKENS = 8` are discarded as noise.

---

### 2.6 `detection/pipeline.py` — combining the evidence

#### `_aggregate` (`pipeline.py:284`) — the subtlest code in the project

```python
weighted  = Σ(score × weight) / Σ(weight)
strongest = max(scores)
return max(weighted, strongest × 0.75)
```

Weights (`COMBINED_WEIGHTS`):

| Signal | Weight | Why |
|---|---|---|
| `rabin_karp` | 0.30 | Verbatim copying is direct evidence |
| `structural` | 0.30 | Survives renaming; strongest signal for code |
| `cosine` | 0.15 | Useful but inflated by shared boilerplate |
| `semantic` | 0.15 | Valuable but noisiest of the three layers |
| `jaccard` | 0.10 | Weakest — vocabulary overlap alone proves little |

**Why the `max(weighted, strongest × 0.75)` floor exists.** A weighted average
lets weak signals dilute a conclusive one. If fingerprinting reports 100%
verbatim overlap, that is decisive — it should not be dragged down to 45%
because vocabulary overlap happened to look ordinary. The floor keeps any single
strong signal visible while still letting agreement between signals push the
score higher.

**Worked example — junaid, the paraphrase case.** His scores against bilal are:

```
rabin_karp 0.0   jaccard 29.5   lcs 33.6   levenshtein 12.7
cosine 52.2      semantic 63.6
```

Weighted average = **27.1**, which is below the 40% threshold — he would not be
flagged at all. But the strongest signal is semantic at 63.6, so the floor gives
`63.6 × 0.75 = 47.7`, and that is his reported score. The floor is the only
reason this paraphrase is caught.

**Be ready for the counter-question:** *doesn't that inflate scores?* It raises
the floor, yes — which is why the threshold sits at 40% and why the report shows
the per-algorithm breakdown. A teacher can always see which signal drove the
number. The score is triage, not a verdict.

#### Marks (`_build_outcome`)

```
deduction = max_marks × (similarity − threshold) ÷ (100 − threshold)
```

Nothing is deducted below the threshold. Above it, the deduction scales with
how far past the threshold the submission sits. At a 40% threshold out of 100
marks: 40% loses nothing, 70% loses 50, 100% loses everything.

**Why not deduct proportionally to raw similarity?** The original code did
exactly that — `max_score × (1 − average/100)` — which punished a 20% similarity
with a 20% deduction, even though 20% overlap between two correct answers to the
same question is entirely normal.

#### Severity bands (`SEVERITY_BANDS`)

critical ≥ 85 · high ≥ 65 · moderate ≥ 45 · low ≥ 25 · else none.

---

### 2.7 `extraction.py` — why this module exists at all

The original passed uploaded files straight to word-splitting. Submissions are
PDFs. **The algorithms were comparing binary bytes**, which is why the
percentages were meaningless.

Handles PDF (via `pypdf`), DOCX, zip, rar, and source files. Archives are read
member by member with guard rails: `MAX_ARCHIVE_MEMBERS = 200` and
`MAX_TOTAL_CHARS = 2,000,000` bound an archive bomb.

**Scanned PDFs** have no text layer. The module says so explicitly rather than
returning empty text that would score as 0% — a silent zero looks like
"original work", which is the dangerous failure.

Extraction is **cached** on the `Submission` row (`services.ensure_extracted`).
Without the cache, a 30-student run would re-parse every PDF 29 times.

---

### 2.8 `permissions.py` and the registration gate

The original build had no authorization beyond `@login_required`:

- `upload_submission` had **no decorator at all** — any user could overwrite any
  student's file by guessing an ID
- `delete_course` accepted **GET** with no ownership check
- No view verified that a teacher owned the course they were editing

Now: `teacher_required` / `student_required` / `admin_required` decorators, plus
`assert_owns_course` / `assert_owns_assignment` for object-level checks.
Destructive actions are POST-only.

**The teacher registration gate** (`forms.py`). Without it, anyone could select
"Teacher" on the public registration page and see every submission in the
system. Registration as a teacher requires a departmental code, validated
server-side, compared with `secrets.compare_digest` (constant-time, so the code
cannot be guessed character by character through response timing). If the code
is unconfigured, teacher self-registration is **disabled** rather than open —
it fails closed.

---

## 3. Numbers to know cold

| Constant | Value | Where |
|---|---|---|
| Winnowing `k` | 12 tokens | `structural.py:33` |
| Winnowing `w` | 8 | `structural.py:34` |
| Guaranteed detection | ≥ 19 tokens | `k + w − 1` |
| Semantic threshold (transformer) | 0.55 | `semantic.py:56` |
| Rabin-Karp window | 5 tokens | `algorithms.py` |
| Quadratic cap | 4,000 tokens | `algorithms.py:32` |
| Passage seed / minimum | 5 / 8 tokens | `highlighting.py` |
| Default flagging threshold | 40% | `settings.py` |

**Performance** — 400-word submissions:

| Class size | Pairs | Lexical | + Structural | + Semantic |
|---|---|---|---|---|
| 20 | 190 | 0.06s | 0.18s | 10.9s |
| 30 | 435 | 0.13s | 0.40s | 24.8s |

Pairs grow as n(n−1)/2. The embedding model costs ~6s to load, once.

**The demo cohort** — the table to have open during the presentation:

| Student | Score | Caught by |
|---|---|---|
| bilal | 100% | Step 3 exact match |
| **zara / omar** | **75%** | **structural=100, rabin_karp=0** |
| hina | 48.5% | rabin_karp=44.8, plus archive match |
| **junaid** | **47.7%** | **semantic=63.6, rabin_karp=0** |
| sana | 16.5% | not flagged |

The two bold rows justify the entire architecture. Lead with them.

---

## 4. Question bank

### Algorithms

**"Why five algorithms instead of one?"**
Each is blind to something. Jaccard ignores order; Rabin-Karp cannot see
paraphrase; LCS misses synonym substitution. The demo shows two cases where the
best lexical algorithm reports exactly 0% and another layer reports 100%.

**"Why winnowing rather than hashing every k-gram?"**
Storing every k-gram is O(document length) fingerprints. Winnowing bounds the
density while still guaranteeing that any match of 19 tokens or more is
detected. It is also robust to insertion, which fixed-interval selection is not.

**"Why is the LCS not reconstructed?"**
Only its length is needed for the score, and length needs just two rows instead
of an m×n table. Matched passages come from `highlighting.py`, which finds
maximal *contiguous* runs — more useful in a report than a scattered
subsequence.

**"How do you know the semantic threshold is correct?"**
It was measured, not guessed — and the first value was wrong. See section 2.4.
State the distributions: paraphrase 0.59–0.85, independent same-topic ≤ 0.44,
threshold 0.55 in the gap.

**"What stops two correct answers scoring as plagiarism?"**
Three things: IDF downweights class-wide boilerplate; the semantic threshold
sits above the same-topic ceiling; and nothing below 40% is flagged at all.

### Architecture

**"Why SQLite and not the PostgreSQL / MongoDB / Redis in your thesis?"**
Those were written as design intent. What is delivered is a Django monolith on
SQLite, sized for a department. The engine has no database code in it, so the
storage layer could be swapped without touching detection.

**"Where is the React frontend from section 4.1.3?"**
Not built. Server-rendered Django templates with Bootstrap. A reasonable
trade-off for the scale, and honest to say so.

**"Why is the pipeline separate from the models?"**
`DetectionPipeline` takes strings and returns objects — no ORM. That is why the
engine can be tested without a database, and why 105 tests run in under a
minute.

### Evaluation

**"How did you evaluate accuracy?"**
A labelled cohort with six known categories — verbatim, partial, paraphrase,
renamed code, recycled, original — and the system separates all six correctly.
Plus the threshold calibration measurements and the timing table.

**"What is your false positive rate?"**
On the calibration set, zero at the chosen thresholds. Be precise about the
sample size — it is small, and that is a genuine limitation, not a result to
overstate.

### The uncomfortable ones

**"Your title says AI content detection. Show me it detecting AI-generated code."**
It does not do that. The system detects copying between students. The AI
component is the transformer used for paraphrase detection, not an
AI-authorship classifier. Say this plainly and early rather than being caught by
it — see section 5.

**"Does it check against the internet or GitHub?"**
No. Comparison covers submissions inside the system, including everything
submitted previously. An external corpus is the obvious next step.

---

## 5. What to concede, and how

Conceding a limitation with the reasoning attached reads as judgement.
Conceding it only when cornered reads as oversight. Volunteer these.

| Limitation | How to put it |
|---|---|
| **No AI-authorship detection** | "The title overreaches. The system detects inter-student copying; the neural model does paraphrase detection, not authorship classification. Building an AI-text classifier reliably is a research problem in itself, and I would rather ship three layers that demonstrably work than a fourth that does not." |
| No external corpus | "Scoped to institutional submissions. The archive check already compares against every prior submission, so extending to an external index is an extraction problem, not an architecture change." |
| Scanned PDFs | "No OCR. The system reports it rather than scoring zero, because a silent zero reads as original work — the dangerous failure." |
| Quadratic algorithms capped | "LCS and Levenshtein are O(m·n). Capped at 4,000 tokens and the result is marked truncated, so the report never hides a partial comparison." |
| Small evaluation set | "The labelled cohort demonstrates separation between categories; it is not large enough for a published accuracy figure." |
| Single instance | "SQLite, no load balancing. Sized for a department, not a university." |

**The framing that ties it together:** a similarity score is evidence, not a
verdict. The report exists so a human reads the matched passages and decides.
Every design choice above — IDF weighting, thresholds above the same-topic
ceiling, deductions only past a threshold, showing the per-algorithm breakdown —
follows from treating a false accusation as the expensive error.
