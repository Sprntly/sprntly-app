FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY mcp_server ./mcp_server
RUN pip install --no-cache-dir .

EXPOSE 8003
CMD ["uvicorn", "mcp_server.__main__:app", "--host", "0.0.0.0", "--port", "8003"]
