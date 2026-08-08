FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python process_emails.py & exec gunicorn app:app --timeout 300 --worker-class gthread --threads 4"]
