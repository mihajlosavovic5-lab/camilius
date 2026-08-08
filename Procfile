web: gunicorn app:app --timeout 300 --worker-class gthread --threads 4
worker: python process_emails.py
