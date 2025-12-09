FROM python:3.11-slim

# Install git and procps (procps provides ps command for health checks)
RUN apt-get update && apt-get install -y git procps && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Run the service
CMD ["python", "main.py"]
