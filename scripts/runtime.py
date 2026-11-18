"""Map host settings to libpq without putting secrets in command arguments."""
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote


def configure():
    if os.environ.get('DATABASE_URL'):
        url = urlsplit(os.environ['DATABASE_URL'])
        if url.scheme not in {'postgres', 'postgresql'} or not url.hostname or not url.path.strip('/'):
            raise ValueError('DATABASE_URL must be a PostgreSQL connection URL with a host and database')
        settings = dict(PGHOST=url.hostname, PGPORT=str(url.port or 5432),
                        PGDATABASE=unquote(url.path[1:]))
        if url.username is not None:
            settings['PGUSER'] = unquote(url.username)
        if url.password is not None:
            settings['PGPASSWORD'] = unquote(url.password)
        parameters = {'sslmode':'PGSSLMODE', 'channel_binding':'PGCHANNELBINDING',
                      'connect_timeout':'PGCONNECT_TIMEOUT', 'sslrootcert':'PGSSLROOTCERT',
                      'application_name':'PGAPPNAME', 'options':'PGOPTIONS'}
        for key, values in parse_qs(url.query).items():
            if key not in parameters or len(values) != 1:
                raise ValueError('Unsupported or repeated database connection parameter')
            settings[parameters[key]] = values[0]
        os.environ.update(settings)
    os.environ.setdefault('PGCONNECT_TIMEOUT', '10')


def initialize():
    configure()
    from src.imports.load import psql
    from src.engine import service
    from src.policy import store
    from src.workspace import auth
    schema = Path(__file__).resolve().parents[1] / 'db/schema.sql'
    psql('BEGIN; SELECT pg_advisory_xact_lock(53422017);' + schema.read_text() + 'COMMIT;')
    service.initialize()
    store.initialize()
    auth.initialize()
