FROM python:3.11-slim
# tesseract: OCR fallback for scanned PDF pages
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# CPU-only torch keeps the image small; sentence-transformers reuses it.
ENV PIP_DEFAULT_TIMEOUT=300 PIP_RETRIES=15
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
# Bake the embedding models into the image so the first request never downloads anything.
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-small-en-v1.5'); S('sentence-transformers/all-MiniLM-L6-v2')"
COPY . .
ENV LLM_MODE=mock RUNTIME_DIR=/app/runtime PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=10 \
    CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
