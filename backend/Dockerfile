FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir .

USER 65532:65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
