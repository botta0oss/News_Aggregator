FROM python:3.11-slim
WORKDIR /app

# Install system dependencies required for pgvector/building packages if needed
RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

CMD["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]