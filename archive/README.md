# Archive — superseded prototypes

Nothing in this folder is part of the running application. It is kept because
it documents how the project was built, and because the standalone script below
is a clean, dependency-free demonstration of the five classical algorithms.

**To run the actual application, see the README one level up.**

| File | What it was |
|---|---|
| `main.py` | Standalone command-line prototype. Compares `TestFiles/file_1.txt` against `file_2.txt` and prints all five classical similarity scores. Still runs. |
| `plagiarism_detection.py` | The algorithm library as first written. Superseded by `plagiarism_detector/academic/detection/`. |
| `index.html` | Early standalone UI: two text areas and an algorithm dropdown. Was never connected to Django; the `script.js` it references does not exist. |
| `code.txt` | A plain-text dump of the source as it stood in June 2024. |
| `TestFiles/` | The two sample documents `main.py` reads. |

## Running the standalone demo

```bash
cd archive
python3 main.py
```

No dependencies needed — it uses only the Python standard library.

## Why these were superseded

The versions in `plagiarism_detector/academic/detection/` fix real defects in
this code:

- `lcs` allocated a full `(m+1) x (n+1)` table; two 5,000-word documents meant
  25 million Python integers. The current version uses two rolling rows.
- `levenshtein_distance` compared raw characters, so a 20,000-character pair
  cost 400 million cell updates. The current version compares tokens.
- `rabin_karp` searched for single whole words and re-hashed the text once per
  word, which measures vocabulary overlap rather than copied passages. The
  current version fingerprints word n-grams in a single pass.
- `compare_assignments` raised `UnboundLocalError` on its `lcs` and
  `levenshtein` branches.

The current engine also adds preprocessing, structural (AST) analysis, semantic
paraphrase detection, passage highlighting and text extraction, none of which
exist here.
