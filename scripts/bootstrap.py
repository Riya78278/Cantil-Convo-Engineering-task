"""Import a private kit or create an account using a private host shell."""
import argparse
import getpass
import sys
import tempfile
from pathlib import Path
from scripts.runtime import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    kit = commands.add_parser('import-kit')
    kit.add_argument('path', help='ZIP path, or - to receive ZIP bytes from standard input')
    account = commands.add_parser('create-user')
    account.add_argument('username')
    account.add_argument('--name', required=True)
    account.add_argument('--role', required=True, choices=['employee', 'buyer', 'procurement'])
    args = parser.parse_args()
    initialize()
    if args.command == 'import-kit':
        from src.imports.load import import_data
        if args.path == '-':
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'kit.zip'
                with path.open('wb') as output:
                    total = 0
                    while chunk := sys.stdin.buffer.read(1024 * 1024):
                        total += len(chunk)
                        if total > 100 * 1024 * 1024:
                            raise ValueError('Private kit exceeds 100 MiB')
                        output.write(chunk)
                result = import_data('kit', path)
        else:
            result = import_data('kit', Path(args.path))
        print(f"Imported {result['rows']} sample lines; {result['flagged']} flagged for review.")
    else:
        from src.workspace.auth import create_user
        password = getpass.getpass('Password (at least 12 characters): ')
        if password != getpass.getpass('Repeat password: '):
            raise ValueError('Passwords did not match')
        create_user(args.username, args.name, args.role, password)
        print('Account created.')


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc))
