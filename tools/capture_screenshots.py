#!/usr/bin/env python3
"""
Capture the screenshots in plagiarism_detector/docs/screenshots/.

    python tools/capture_screenshots.py

Requires the development dependencies and a running server:

    pip install -r requirements-dev.txt
    cd plagiarism_detector && python manage.py runserver

Drives the Chrome already installed on the machine (``channel="chrome"``), so
no separate browser is downloaded.

Two details worth knowing before changing this file:

* Object IDs are looked up from the database at run time, never hardcoded.
  Re-seeding the demo assigns new primary keys, so a hardcoded ID silently
  starts pointing at nothing (or, worse, at the wrong record).
* Taking a screenshot writes to the activity log, because ``login_user`` and
  ``report_detail`` both call ``log_activity``. The administrator pages are
  therefore captured first, immediately after re-seeding, so the activity log
  image shows the seeded 2024 history rather than a screenful of today.
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / 'plagiarism_detector'
OUT = PROJECT / 'docs' / 'screenshots'
BASE_URL = os.environ.get('SMARTANALYTICA_URL', 'http://127.0.0.1:8000')
PASSWORD = 'Demo!12345'

VIEWPORT = {'width': 1440, 'height': 900}
SCALE = 2

sys.path.insert(0, str(PROJECT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'plagiarism_detector.settings')


# --------------------------------------------------------------------------
# Looking up the records to photograph
# --------------------------------------------------------------------------

def load_ids() -> dict:
    """Read the demo record IDs straight from the database."""
    import django
    django.setup()
    from academic.models import Assignment, Course, PlagiarismReport

    course = Course.objects.filter(course_code='CS-3033').first()
    assignment = Assignment.objects.filter(title__icontains='Sorting').first()
    report = PlagiarismReport.objects.filter(assignment=assignment).order_by('-id').first()
    if not (course and assignment and report):
        sys.exit('Demo data missing. Run:  python manage.py seed_demo --reset')

    # Pick the two most illustrative pairs rather than simply the highest
    # scoring one: the top match is usually the exact duplicate, which has a
    # single passage and nothing to show for the semantic layer.
    matches = list(report.matches.all())
    both = sorted(
        (m for m in matches if m.matched_passages and m.paraphrase_matches),
        key=lambda m: len(m.matched_passages) + len(m.paraphrase_matches),
        reverse=True,
    )
    paraphrase_only = sorted(
        (m for m in matches if m.paraphrase_matches and not m.matched_passages),
        key=lambda m: len(m.paraphrase_matches),
        reverse=True,
    )

    return {
        'course': course.id,
        'assignment': assignment.id,
        'report': report.id,
        'match_both': both[0].id if both else matches[0].id,
        'match_paraphrase': paraphrase_only[0].id if paraphrase_only else matches[0].id,
    }


# --------------------------------------------------------------------------
# Capture helpers
# --------------------------------------------------------------------------

class Session:
    def __init__(self, page):
        self.page = page
        self.count = 0
        self.failures: list[str] = []

    def login(self, username: str) -> None:
        self.page.goto(f'{BASE_URL}/logout/', wait_until='networkidle')
        self.page.goto(f'{BASE_URL}/login/', wait_until='networkidle')
        self.page.fill('input[name="username"]', username)
        self.page.fill('input[name="password"]', PASSWORD)
        self.page.click('button[type="submit"]')
        self.page.wait_for_load_state('networkidle')

    def shot(self, name: str, path: str, *, before=None) -> None:
        """Navigate to `path`, optionally run `before`, then capture full page."""
        response = self.page.goto(f'{BASE_URL}{path}', wait_until='networkidle')
        status = response.status if response else 0

        if status != 200:
            self.failures.append(f'{name}: HTTP {status} at {path}')
            print(f'  SKIPPED {name:<34} HTTP {status}')
            return

        body = self.page.content()
        for marker in ('Server Error', 'Traceback (most recent call last)'):
            if marker in body:
                self.failures.append(f'{name}: page contains "{marker}"')
                print(f'  SKIPPED {name:<34} error page')
                return

        if before:
            before(self.page)
            self.page.wait_for_timeout(250)

        target = OUT / f'{name}.png'
        self.page.screenshot(path=str(target), full_page=True)
        self.count += 1
        size_kb = target.stat().st_size / 1024
        print(f'  {name:<42} {size_kb:6.0f} KB')

    def shot_terminal(self, name: str, title: str, output: str) -> None:
        """Render command output as a terminal window and photograph it."""
        page_html = TERMINAL_TEMPLATE.format(
            title=html.escape(title),
            body=html.escape(output.rstrip()),
        )
        self.page.set_content(page_html, wait_until='load')
        target = OUT / f'{name}.png'
        # Shoot the window element, not the page: a full-page capture would
        # pad short output with most of a blank 900px viewport.
        self.page.locator('.win').screenshot(path=str(target))
        self.count += 1
        print(f'  {name:<42} {target.stat().st_size / 1024:6.0f} KB')


TERMINAL_TEMPLATE = """
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  body {{ margin: 0; padding: 28px; background: #eef1f6;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .win {{ background: #10203a; border-radius: 10px; overflow: hidden;
          box-shadow: 0 8px 28px rgba(16,32,58,.22); }}
  .bar {{ background: #1c2e4d; padding: 10px 14px; display: flex;
          align-items: center; gap: 8px; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; }}
  .r {{ background:#ff5f57 }} .y {{ background:#febc2e }} .g {{ background:#28c840 }}
  .title {{ color: #9fb3d1; font-size: 12px; margin-left: 10px; }}
  pre {{ margin: 0; padding: 18px 20px; color: #dbe6f5; font-size: 13px;
         line-height: 1.55; white-space: pre-wrap; word-break: break-word; }}
</style></head><body>
  <div class="win">
    <div class="bar"><span class="dot r"></span><span class="dot y"></span>
      <span class="dot g"></span><span class="title">{title}</span></div>
    <pre>{body}</pre>
  </div>
</body></html>
"""


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    return (result.stdout or '') + (result.stderr or '')


# --------------------------------------------------------------------------
# The capture run
# --------------------------------------------------------------------------

def open_select(name: str):
    """Reveal a <select>'s options by turning it into a list box."""
    def action(page):
        page.eval_on_selector(
            f'select[name="{name}"]',
            'el => { el.size = el.options.length; el.setAttribute("open", "true"); }',
        )
    return action


def choose_teacher_role(page):
    """Tick 'Teacher' so the access-code field is visible."""
    page.check('input[name="role"][value="teacher"]')


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    ids = load_ids()
    print(f'\nCapturing to {OUT.relative_to(ROOT)}\n')

    python = ROOT / '.venv' / 'bin' / 'python'

    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
        s = Session(context.new_page())

        # --- Administrator first, so the activity log stays clean ---------
        print('Administrator')
        s.login('demo_admin')
        s.shot('19-admin-dashboard', '/admin-panel/')
        s.shot('20-activity-log', '/admin-panel/activity/')
        s.shot('21-integrity-monitor', '/admin-panel/integrity/')

        # --- Public pages -------------------------------------------------
        print('\nPublic')
        s.page.goto(f'{BASE_URL}/logout/', wait_until='networkidle')
        s.shot('01-home', '/')
        s.shot('02-register', '/register/', before=choose_teacher_role)
        s.shot('03-login', '/login/')

        # --- Teacher ------------------------------------------------------
        print('\nTeacher')
        s.login('demo_teacher')
        s.shot('04-teacher-dashboard', '/teacher/')
        s.shot('05-manage-courses', '/courses/')
        s.shot('06-create-course', '/courses/new/')
        s.shot('07-create-assignment', '/assignments/new/')
        s.shot('08-view-assignments', '/assignments/')
        s.shot('09-enrol-students', '/courses/enroll/')
        s.shot('10-course-details', f'/courses/{ids["course"]}/')

        # --- Detection and reports ----------------------------------------
        print('\nDetection and reports')
        s.shot('11-reports-index', '/reports/')
        s.shot('12-run-detection', f'/reports/run/{ids["assignment"]}/',
               before=open_select('method'))
        s.shot('13-report-detail', f'/reports/{ids["report"]}/')
        s.shot('14-match-verbatim-and-paraphrase', f'/reports/match/{ids["match_both"]}/')
        s.shot('15-match-paraphrase-only', f'/reports/match/{ids["match_paraphrase"]}/')

        # --- Student ------------------------------------------------------
        print('\nStudent')
        s.login('demo_ayesha')
        s.shot('16-student-dashboard', '/student/')
        s.shot('17-submit-assignment', f'/student/submit/{ids["assignment"]}/')
        s.shot('18-my-results', '/student/results/')

        # --- Terminal evidence ---------------------------------------------
        print('\nTerminal')
        tests = run([str(python), 'manage.py', 'test', 'academic', '-v', '1'], PROJECT)
        s.shot_terminal('22-tests-passing', 'python manage.py test academic',
                        clean(tests))
        detection = run([str(python), 'manage.py', 'seed_demo', '--reset'], PROJECT)
        s.shot_terminal('23-detection-run', 'python manage.py seed_demo --reset',
                        clean(detection))

        browser.close()

    print(f'\n{s.count} screenshots written.')
    if s.failures:
        print('\nFailures:')
        for failure in s.failures:
            print(f'  - {failure}')
        sys.exit(1)


def clean(output: str) -> str:
    """Drop the LibreSSL warning macOS emits, which is noise in a screenshot."""
    skip = ('NotOpenSSLWarning', 'warnings.warn', 'urllib3/__init__.py')
    return '\n'.join(l for l in output.splitlines() if not any(s in l for s in skip))


if __name__ == '__main__':
    main()
