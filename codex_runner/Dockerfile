FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" CODEX_HOME=/home/codex/.codex

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git gh ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global @openai/codex \
    && python3 -m venv /opt/venv

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY __init__.py main.py ./codex_runner/

RUN useradd --create-home --uid 10001 codex \
    && mkdir -p /home/codex/.codex /workspaces \
    && chown -R codex:codex /home/codex /workspaces

USER codex
EXPOSE 7000
CMD ["uvicorn", "codex_runner.main:app", "--host", "0.0.0.0", "--port", "7000"]
