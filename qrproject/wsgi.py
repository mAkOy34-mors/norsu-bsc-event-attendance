"""
WSGI config for qrproject project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import sys

# When executed directly ("python qrproject/wsgi.py"), sys.path[0] is the
# qrproject/ dir itself, so the DJANGO_SETTINGS_MODULE import can't find the
# 'qrproject' package. Fix the path before Django loads. ( Harmless when the
# module is imported normally, e.g. by "waitress-serve".)
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qrproject.settings')

application = get_wsgi_application()

# --- Production entrypoint (Waitress) ---------------------------------------
# "python qrproject/wsgi.py" serves the app with Waitress instead of the dev
# server. Thread count matters for the scan endpoint: Waitress defaults to
# only 4 threads, so a burst from ~22 scanners queues up (the
# "Task queue depth is N" warning) and the tail of the burst waits seconds.
# One DB connection is held per thread (CONN_MAX_AGE), so keep this well under
# MariaDB's max_connections (151 on this deployment).
if __name__ == "__main__":
    from waitress import serve

    serve(
        application,
        host=os.getenv("WAITRESS_HOST", "0.0.0.0"),
        port=int(os.getenv("WAITRESS_PORT", "8001")),
        threads=int(os.getenv("WAITRESS_THREADS", "32")),
    )

