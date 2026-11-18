"""Run regressions using a private kit and disposable LOCAL PostgreSQL databases."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    args = parser.parse_args()
    if os.environ.get('DATABASE_URL') or '://' in os.environ.get('PGDATABASE', ''):
        raise SystemExit('Use a local test PostgreSQL instance, not a hosted DATABASE_URL.')
    root = Path(__file__).resolve().parents[1]
    suites = ['check_phase_one', 'check_phase_two', 'check_phase_three', 'check_batch_api',
              'check_phase_four', 'check_phase_five', 'check_preferred_export', 'check_deployment']
    for suite in suites:
        command = [sys.executable, '-B', str(root / 'tests' / (suite + '.py'))]
        if suite not in ['check_preferred_export', 'check_deployment']:
            command.append(str(args.kit.resolve()))
        subprocess.run(command, cwd=root, check=True)
