FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download pt_core_news_sm && \
    python -m spacy download fr_core_news_sm

COPY run.py .

ENTRYPOINT ["python", "run.py"]
