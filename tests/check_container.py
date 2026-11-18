"""Disposable Docker deployment rehearsal. Requires Docker and a private task kit."""
import argparse
import csv
import io
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('--image', default='corvo-procurement:phase6')
    args = parser.parse_args()
    prefix = 'corvo-check-' + uuid.uuid4().hex[:10]
    db, web = prefix + '-db', prefix + '-web'
    password = secrets.token_urlsafe(24)
    env = dict(os.environ, POSTGRES_PASSWORD=password,
               DATABASE_URL=f'postgresql://postgres:{password}@{db}/postgres')
    def docker(*parts, data=None):
        result = subprocess.run(['docker', *parts], input=data, capture_output=True, env=env)
        if result.returncode:
            raise RuntimeError(result.stderr.decode().replace(password, '[redacted]'))
        return result.stdout.decode().strip()
    cookie = ''
    base = ''
    def api(path, payload=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        request = Request(base + path, data=body, headers={
            'Content-Type': 'text/csv' if raw is not None else 'application/json', 'Cookie': cookie})
        with urlopen(request, timeout=30) as response:
            content = response.read()
            if response.headers.get_content_type() == 'application/json':
                content = json.loads(content)
            return content, response.headers
    def wait_ready():
        for _ in range(60):
            try:
                api('/')
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError('Container did not become ready')
    try:
        docker('network', 'create', prefix)
        docker('run', '-d', '--name', db, '--network', prefix, '-e', 'POSTGRES_PASSWORD', 'postgres:16-bookworm')
        for _ in range(60):
            try:
                docker('exec', db, 'pg_isready', '-U', 'postgres')
                break
            except RuntimeError:
                time.sleep(1)
        docker('run', '-d', '--name', web, '--network', prefix, '-e', 'DATABASE_URL',
               '-p', '127.0.0.1::8000', args.image)
        address = docker('port', web, '8000/tcp')
        base = 'http://' + address
        wait_ready()
        print('PASS fresh DATABASE_URL startup and HTTP health', flush=True)
        for _ in range(2):
            docker('exec', '-i', web, 'python', '-m', 'scripts.bootstrap', 'import-kit', '-', data=args.kit.read_bytes())
        print('PASS private stdin kit import and identical reimport', flush=True)
        source = "from scripts.runtime import initialize; initialize(); from src.workspace.auth import create_user; create_user('testreviewer','Test reviewer','procurement','Synthetic-container-password-123')"
        docker('exec', web, 'python', '-c', source)
        _, headers = api('/api/login', {'username':'testreviewer','password':'Synthetic-container-password-123'})
        cookie = headers['Set-Cookie'].split(';')[0]
        with ZipFile(args.kit) as kit:
            batch = kit.read(next(n for n in kit.namelist() if n.endswith('/sample_pr_batch.csv')))
        run1, _ = api('/api/process-batch?policy_version=5.0', raw=batch)
        draft, _ = api('/api/policy-drafts', {'base_version':'5.0','version':'5.1',
                                            'effective_date':'2026-11-01','text':'Set spot limit to INR 100000'})
        endpoint = '/api/policy-drafts/' + draft['draft_id']
        draft, _ = api(endpoint+'/save', {'revision':draft['revision'],'candidate':draft['candidate'],'reviewed':True})
        api(endpoint+'/approve', {'revision':draft['revision']})
        run2, _ = api('/api/process-batch?policy_version=5.1', raw=batch)
        assert len(run1['decisions']) == len(run2['decisions']) == 186
        assert sum(a['route'] != b['route'] for a,b in zip(run1['decisions'],run2['decisions'])) == 47
        export, _ = api('/api/preferred-suppliers.csv?as_of=2026-11-01&policy_version=5.1')
        rows = list(csv.DictReader(io.StringIO(export.decode())))
        assert len(rows) == 155 and len({r['item_code'] for r in rows}) == 68
        # Compare the container with local calculations, not only aggregate totals.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.imports.load import load_kit
        from src.engine.core import Engine
        from src.engine.preferred_export import render
        from src.engine.service import POLICY_PATH
        tables, _, _ = load_kit(args.kit)
        policies = [json.loads(POLICY_PATH.read_text()), draft['candidate']]
        for run, timeline in [(run1,policies[:1]),(run2,policies)]:
            assert run['decisions'] == Engine(tables,timeline).process(batch)['decisions']
        assert export.decode() == render(dict(tables,policies=policies),'2026-11-01','5.1')
        print('PASS authenticated policy amendment, both decision runs and preferred CSV match local calculations', flush=True)
        docker('restart', web)
        base = 'http://' + docker('port', web, '8000/tcp')
        wait_ready()
        replay, _ = api('/api/replay-run', {'run_id':run1['run_id']})
        assert replay == run1
        assert api('/api/preferred-suppliers.csv?as_of=2026-11-01&policy_version=5.1')[0] == export
        print('PASS restart preserves session, policies, saved runs and private data', flush=True)
        uid = docker('exec', web, 'id', '-u')
        assert uid != '0'
        files = docker('exec', web, 'find', '/app', '-type', 'f').splitlines()
        assert not any(p.endswith(('.zip','.pdf','.csv','.dump')) or '/.env' in p for p in files)
        print('PASS non-root image contains no supplied ZIP/PDF/CSV or environment files', flush=True)
    finally:
        subprocess.run(['docker','rm','-f',web,db],capture_output=True)
        subprocess.run(['docker','network','rm',prefix],capture_output=True)


if __name__ == '__main__':
    main()
