FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn psycopg2-binary

COPY backend/ .

# Static files are served by whitenoise; collectstatic needs no database.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --uid 1000 --no-create-home app
USER 1000

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
