FROM moby/buildkit:v0.24.0 AS buildkit

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# buildctl's client-side auth provider needs a writable token-seed directory.
ENV DOCKER_CONFIG=/data/.docker
ARG TASK_RUNNER_SOURCE_SHA=unknown
ENV TASK_RUNNER_SOURCE_SHA=${TASK_RUNNER_SOURCE_SHA}
WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=buildkit /usr/bin/buildctl /usr/local/bin/buildctl
COPY task_runner ./task_runner
COPY codex_runner ./codex_runner
COPY scripts ./scripts

RUN mkdir -p /data && chown -R nobody:nogroup /data
USER nobody
EXPOSE 6002

CMD ["uvicorn", "task_runner.main:app", "--host", "0.0.0.0", "--port", "6002"]
