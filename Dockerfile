FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PORT=8080

RUN groupadd --system clicknfe \
    && useradd --system --gid clicknfe --home-dir /app clicknfe

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=clicknfe:clicknfe app ./app
COPY --chown=clicknfe:clicknfe migrations ./migrations
COPY --chown=clicknfe:clicknfe wsgi.py gunicorn.conf.py ./

USER clicknfe

EXPOSE 8080

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]


FROM runtime AS test

USER root
COPY --chown=clicknfe:clicknfe requirements.txt requirements-dev.txt pytest.ini ./
COPY --chown=clicknfe:clicknfe tests ./tests
RUN pip install --no-cache-dir -r requirements-dev.txt
USER clicknfe

CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
