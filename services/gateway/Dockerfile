FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir grpcio grpcio-tools fastapi uvicorn
COPY services/gateway/ ./services/gateway/
COPY services/graph_service/generated/ ./services/graph_service/generated/
COPY services/qa_service/generated/ ./services/qa_service/generated/
COPY services/__init__.py ./services/__init__.py
EXPOSE 8000
CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
