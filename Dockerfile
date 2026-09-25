# CPU-only image (servers, VPS, ARM machines). For a local NVIDIA GPU use Dockerfile.cuda
# (docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build).
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies required for pgvector/building packages if needed
RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# PyTorch from the CPU-only index first: sentence-transformers then finds it installed and does
# not pull the default build with CUDA (several GB of GPU libraries a server without GPU never uses)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && python -c "import torch; assert torch.version.cuda is None, 'CUDA build of torch installed in the CPU image'"

# Pre-download the embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

# --proxy-headers: behind a reverse proxy, use the real client IP (login rate limit) and scheme (HSTS)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
