FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY restore_backup.py .
COPY set_admin_password.py .
COPY security_rules.yaml .
COPY templates ./templates
RUN mkdir -p /app/data /app/secrets
EXPOSE 8080
CMD ["sh", "-c", "python -m app.bootstrap && exec uvicorn app.main:app --host ${BIND_HOST:-0.0.0.0} --port ${PORT:-8080}"]
