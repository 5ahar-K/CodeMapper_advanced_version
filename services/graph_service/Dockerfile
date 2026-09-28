FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir networkx grpcio grpcio-tools google-genai
COPY codemapper/ ./codemapper/
COPY services/graph_service/ ./services/graph_service/
COPY services/__init__.py ./services/__init__.py
COPY sample_repo/ ./sample_repo/
EXPOSE 50051
CMD ["python", "-m", "services.graph_service.server"]
