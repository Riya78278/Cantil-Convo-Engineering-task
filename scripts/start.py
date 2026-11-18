"""Start the hosted application against an existing or empty private database."""
import os
from http.server import ThreadingHTTPServer
from scripts.runtime import initialize


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise SystemExit('PORT must be between 1 and 65535')
    initialize()
    from src.api.server import Handler
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
