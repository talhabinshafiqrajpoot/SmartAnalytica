# Screenshots

Captures of SmartAnalytica running, taken from the live application at
`127.0.0.1:8000` against the seeded demo cohort.

All images are full-page, 1440 × 900 viewport at 2× density (2880 px wide), so
they stay sharp when placed in a printed report.

**To regenerate:** start the server, then

```bash
pip install -r requirements-dev.txt
python tools/capture_screenshots.py
```

These map to **section 4.3 "User Interface"** of the project documentation,
which describes the interface but contains no images.

---

## Public pages

| # | File | Shows |
|---|---|---|
| 01 | `01-home.png` | Landing page, describing the three analysis layers |
| 02 | `02-register.png` | Registration with *Teacher* selected, revealing the **teacher access code** field that prevents a student granting themselves teacher access |
| 03 | `03-login.png` | Sign-in |

## Teacher — courses and assignments

| # | File | Shows |
|---|---|---|
| 04 | `04-teacher-dashboard.png` | Course, assignment, submission and flagged counts; recent detection runs |
| 05 | `05-manage-courses.png` | Course list with student and assignment counts |
| 06 | `06-create-course.png` | Course creation form |
| 07 | `07-create-assignment.png` | Assignment creation, including submission format and maximum marks |
| 08 | `08-view-assignments.png` | Assignments with submission counts and a direct "Check" action |
| 09 | `09-enrol-students.png` | Enrolling students into a course |
| 10 | `10-course-details.png` | Course detail with enrolled students and its assignments |

## Detection and reports — the core of the project

| # | File | Shows |
|---|---|---|
| 11 | `11-reports-index.png` | Past detection runs with timings and flagged counts |
| 12 | `12-run-detection.png` | **All eight algorithm choices** in the selection dropdown, with the flagging threshold. This is documented test case **TC7** |
| 13 | `13-report-detail.png` | The full report: similarity index per student, severity bands, marks with deductions, the **recycled work** badge, and the per-algorithm breakdown for every flagged pair |
| 14 | `14-match-verbatim-and-paraphrase.png` | Side-by-side pair showing **both** a verbatim matched passage and paraphrased sentences |
| 15 | `15-match-paraphrase-only.png` | **Rabin-Karp 0.0%, Semantic 63.6%** — zero verbatim overlap, seven paraphrased passages caught. The clearest single illustration of what the semantic layer adds |

## Student

| # | File | Shows |
|---|---|---|
| 16 | `16-student-dashboard.png` | Enrolled courses, assignments, submission status |
| 17 | `17-submit-assignment.png` | Upload form with the brief and extraction details |
| 18 | `18-my-results.png` | The student's own similarity result and marks |

## Administrator

| # | File | Shows |
|---|---|---|
| 19 | `19-admin-dashboard.png` | Institution-wide totals and recent activity |
| 20 | `20-activity-log.png` | Filterable audit trail across the semester: course creation, enrolment, every student login and submission, and the detection run |
| 21 | `21-integrity-monitor.png` | Flagged submissions ranked by similarity, with repeat-offender tracking |

## Terminal evidence

| # | File | Shows |
|---|---|---|
| 22 | `22-tests-passing.png` | `manage.py test academic` — **105 tests, OK** |
| 23 | `23-detection-run.png` | A live detection run: all seven students ranked with similarity, severity and marks, including the renamed-code and paraphrase cases |

---

## Notes on the data

The demo cohort is dated to the **spring 2024 semester** — the course runs
5 February to 28 June 2024, the assignment was due 14 June, submissions arrive
10–14 June, and the teacher ran detection on 15 June at 10:30.

The cohort deliberately contains one of each case the detector must
distinguish:

| Student | Work | Result |
|---|---|---|
| ayesha | The original | 100% (matched by bilal's copy) |
| bilal | Verbatim copy | 100% — exact duplicate |
| zara / omar | Same code, every variable renamed | 75% — structural 100%, Rabin-Karp 0% |
| hina | Recycled from a previous semester | 48.5% — plus an archive match |
| junaid | Paraphrase, no shared wording | 47.7% — semantic 63.6%, Rabin-Karp 0% |
| sana | Independent work | 16.5% — not flagged |

In `20-activity-log.png` the topmost entry carries the current date: signing in
to take the screenshot is itself a logged event. Everything below it is the
seeded 2024 history.
