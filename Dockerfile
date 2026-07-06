FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY task_runner ./task_runner

RUN mkdir -p /data && chown -R nobody:nogroup /data
USER nobody
EXPOSE 6002

CMD ["uvicorn", "task_runner.main:app", "--host", "0.0.0.0", "--port", "6002"]

