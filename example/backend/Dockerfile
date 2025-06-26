FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Copy the entire repository to install tuspyserver from workspace
COPY . .

# Set working directory for the backend
WORKDIR /app/example/backend

# Expose port
EXPOSE 8000

# Run the server
CMD ["uv", "run", "server.py"]
