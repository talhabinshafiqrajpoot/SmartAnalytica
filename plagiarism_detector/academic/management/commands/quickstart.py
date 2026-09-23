"""
One command that takes a fresh copy of the project to a working demo.

    python manage.py quickstart

Runs migrations, seeds the demo cohort and produces a finished plagiarism
report. Safe to re-run: use --reset to rebuild the demo data from scratch.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Migrate the database and load a complete, ready-to-show demo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Rebuild the demo data from scratch.',
        )
        parser.add_argument(
            '--no-report', action='store_true',
            help='Skip the detection run.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('1/2  Preparing the database'))
        call_command('migrate', verbosity=0)
        self.stdout.write('     done\n')

        self.stdout.write(self.style.MIGRATE_HEADING('2/2  Loading demo data'))
        call_command(
            'seed_demo',
            reset=options['reset'],
            no_report=options['no_report'],
        )
