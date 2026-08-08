#!/bin/sh
# Runs the email-polling worker in the background and gunicorn in the foreground, as
# the container's main process. A dedicated script file (rather than passing this as an
# inline "A & exec B" string to whatever invokes it) guarantees this always goes through
# a real shell that understands `&`/`exec`, regardless of how the caller (Docker CMD vs
# Railway's own "start" override) runs it - an inline string was silently only running
# the first command and never reaching gunicorn at all.
set -e

python process_emails.py &
exec gunicorn app:app --bind "0.0.0.0:${PORT:-8080}" --timeout 300 --worker-class gthread --threads 4
