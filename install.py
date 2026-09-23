#!/usr/bin/env python3
"""
One-command setup for SmartAnalytica.

Run this with your system Python from the folder that contains it:

    python3 install.py          (macOS / Linux)
    python install.py           (Windows)

It creates a virtual environment, installs the dependencies, generates a
SECRET_KEY, prepares the database and loads a complete demo with a finished
plagiarism report. Safe to run more than once.

Only the Python standard library is used here, because nothing is installed yet
when this script starts.
"""

from __future__ import annotations

import os
import platform
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / '.venv'
PROJECT = ROOT / 'plagiarism_detector'
ENV_FILE = PROJECT / '.env'

MIN_PYTHON = (3, 9)
# Code needed to self-register as a teacher. Change it for real use.
TEACHER_CODE = 'GCU-TEACHER-2024'
IS_WINDOWS = platform.system() == 'Windows'


def venv_python() -> Path:
    return VENV / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')


def say(message: str = '') -> None:
    """
    Print and flush immediately.

    Without the flush, Python buffers this script's output whenever it is
    piped or redirected, while the subprocesses write straight through. The
    result is that setup steps appear *after* the output of the commands they
    describe, which reads as though the script ran backwards.
    """
    print(message, flush=True)


def step(number: int, total: int, message: str) -> None:
    say(f'\n[{number}/{total}] {message}')


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(
            f'Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required. '
            f'You are running {platform.python_version()}.\n'
            'Install a newer Python from https://www.python.org/downloads/'
        )
    say(f'    Python {platform.python_version()} on {platform.system()} — OK')


def create_venv() -> None:
    if venv_python().exists():
        say('    Virtual environment already exists, reusing it.')
        return
    run([sys.executable, '-m', 'venv', str(VENV)])
    say(f'    Created {VENV.name}/')


def install_dependencies(with_semantic: bool) -> None:
    python = str(venv_python())
    run([python, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
    run([python, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt'), '--quiet'])
    say('    Core dependencies installed.')

    if with_semantic:
        say('    Installing sentence-transformers (~320 MB, this takes a while)...')
        try:
            run([python, '-m', 'pip', 'install', 'sentence-transformers', '--quiet'])
            say('    Semantic analysis enabled.')
        except subprocess.CalledProcessError:
            say('    Could not install sentence-transformers. The app will use')
            say('    the scikit-learn fallback, which is weaker at paraphrase')
            say('    detection but works. You can install it later.')
    else:
        say('    Skipped sentence-transformers. The scikit-learn fallback will')
        say('    be used; paraphrase detection will be weaker.')


def write_env() -> None:
    if ENV_FILE.exists():
        say('    .env already exists, leaving it alone.')
        return
    # Same alphabet Django uses for get_random_secret_key.
    alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
    key = ''.join(secrets.choice(alphabet) for _ in range(50))
    ENV_FILE.write_text(
        f'SECRET_KEY={key}\n'
        'DEBUG=True\n'
        'ALLOWED_HOSTS=127.0.0.1,localhost\n'
        f'TEACHER_ACCESS_CODE={TEACHER_CODE}\n',
        encoding='utf-8',
    )
    say(f'    Wrote {ENV_FILE.relative_to(ROOT)} with a freshly generated SECRET_KEY.')
    say(f'    Teacher access code set to: {TEACHER_CODE}')


def prepare_demo() -> None:
    run([str(venv_python()), 'manage.py', 'quickstart'], cwd=str(PROJECT))


def print_next_steps() -> None:
    activate = '.venv\\Scripts\\activate' if IS_WINDOWS else 'source .venv/bin/activate'
    runner = venv_python().relative_to(ROOT)
    say('\n' + '=' * 62)
    say('  Setup complete.')
    say('=' * 62)
    say('\n  Start the server:\n')
    say(f'      cd {PROJECT.name}')
    say(f'      {".." / runner} manage.py runserver')
    say('\n  Then open  http://127.0.0.1:8000')
    say('\n  Sign in with:')
    say('      demo_teacher / Demo!12345    (teacher)')
    say('      demo_admin   / Demo!12345    (administrator)')
    say('      demo_ayesha  / Demo!12345    (student)')
    say('\n  A finished plagiarism report is already waiting under Reports.')
    say(f'\n  To register a NEW teacher account you need the access code:  {TEACHER_CODE}')
    say('  Change it in plagiarism_detector/.env before any real deployment.')
    say(f'\n  (If you prefer to activate the environment first: {activate})\n')


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    semantic = '--no-semantic' not in sys.argv
    total = 5

    say('=' * 62)
    say('  SmartAnalytica — setup')
    say('=' * 62)

    step(1, total, 'Checking Python version')
    check_python()

    step(2, total, 'Creating the virtual environment')
    create_venv()

    step(3, total, 'Installing dependencies')
    install_dependencies(semantic)

    step(4, total, 'Generating configuration')
    write_env()

    step(5, total, 'Preparing the database and demo data')
    prepare_demo()

    print_next_steps()


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f'\nSetup failed while running: {" ".join(exc.cmd)}\n')
    except KeyboardInterrupt:
        sys.exit('\nCancelled.\n')
