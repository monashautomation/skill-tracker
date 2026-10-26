FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn psycopg2-binary

COPY backend/ .

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
