FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/*.py .
COPY frontend/ /app/frontend/

ENV DB_PATH=/data/running.db
ENV GARMIN_TOKEN_DIR=/data/garmin_tokens
ENV FRONTEND_DIR=/app/frontend

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
