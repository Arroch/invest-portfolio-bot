# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_EXTRA_INDEX_URL="https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple"
RUN pip install --upgrade pip \
    && pip install .


FROM python:3.12-slim AS runtime

RUN groupadd -r bot && useradd -r -g bot -d /app bot \
    && mkdir -p /app && chown bot:bot /app

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --chown=bot:bot \
    bot.py \
    config.py \
    target.py \
    tinvest.py \
    models.py \
    portfolio.py \
    rebalance.py \
    handlers.py \
    formatting.py \
    ./

USER bot
CMD ["python", "bot.py"]
