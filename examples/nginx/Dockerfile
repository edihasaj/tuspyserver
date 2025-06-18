FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn tuspyserver

# Copy application
COPY server.py .

# Create uploads directory
RUN mkdir -p /app/uploads

# Run the application
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
