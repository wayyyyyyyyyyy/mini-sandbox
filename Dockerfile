FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSPACE=/workspace

WORKDIR /srv/mini-agent-sandbox

RUN useradd --create-home --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace /srv/mini-agent-sandbox

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

USER sandbox
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

