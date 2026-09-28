FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir networkx grpcio grpcio-tools google-genai
COPY codemapper/ ./codemapper/
COPY services/qa_service/ ./services/qa_service/
COPY services/graph_service/generated/ ./services/graph_service/generated/
COPY services/__init__.py ./services/__init__.py
COPY sample_repo/ ./sample_repo/
EXPOSE 50052
CMD ["python", "-m", "services.qa_service.server"]
