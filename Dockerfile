# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11.16-slim-bookworm
ARG TORCH_VERSION=2.11.0

FROM ${PYTHON_IMAGE} AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TRANSCRIPTS_DIR=/data/transcripts \
    WORK_DIR=/data/work \
    WHISPER_CACHE_DIR=/models

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 transcriber \
    && useradd --uid 10001 --gid transcriber --create-home --home-dir /home/transcriber transcriber \
    && install --directory --owner=transcriber --group=transcriber \
        /app /data/transcripts /data/work /models

WORKDIR /app

COPY requirements.txt /app/requirements.txt

FROM base AS dependencies-cpu
ARG TORCH_VERSION
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}+cpu" \
    && python -m pip install --requirement /app/requirements.txt

FROM base AS dependencies-gpu
ARG TORCH_VERSION
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cu128 \
        "torch==${TORCH_VERSION}+cu128" \
    && python -m pip install --requirement /app/requirements.txt

FROM dependencies-cpu AS runtime-cpu
RUN python -m pip install --upgrade \
        "setuptools==81.0.0" \
        "wheel==0.48.0" \
        "jaraco.context==6.1.2"
COPY --chown=transcriber:transcriber src /app/src
COPY --chown=transcriber:transcriber LICENSE THIRD_PARTY_NOTICES.md /app/
LABEL org.opencontainers.image.title="Transcription Automation" \
      org.opencontainers.image.description="Private local audio and video transcription with Whisper" \
      org.opencontainers.image.source="https://github.com/TheSuperDenis/Transcription-Automation" \
      org.opencontainers.image.licenses="MIT"
USER transcriber
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=4", "--timeout=0", "--no-control-socket", "--access-logfile=-", "--error-logfile=-", "transcriber_app:create_app()"]

FROM dependencies-gpu AS runtime-gpu
RUN python -m pip install --upgrade \
        "setuptools==81.0.0" \
        "wheel==0.48.0" \
        "jaraco.context==6.1.2"
COPY --chown=transcriber:transcriber src /app/src
COPY --chown=transcriber:transcriber LICENSE THIRD_PARTY_NOTICES.md /app/
LABEL org.opencontainers.image.title="Transcription Automation" \
      org.opencontainers.image.description="Private local audio and video transcription with Whisper" \
      org.opencontainers.image.source="https://github.com/TheSuperDenis/Transcription-Automation" \
      org.opencontainers.image.licenses="MIT"
USER transcriber
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=4", "--timeout=0", "--no-control-socket", "--access-logfile=-", "--error-logfile=-", "transcriber_app:create_app()"]
