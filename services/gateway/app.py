"""gateway: the only service exposed to the outside world."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import grpc
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "graph_service" / "generated"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qa_service" / "generated"))

import graph_service_pb2 as graph_pb2
import graph_service_pb2_grpc as graph_pb2_grpc
import qa_service_pb2 as qa_pb2
import qa_service_pb2_grpc as qa_pb2_grpc

app = FastAPI(title="Code-Mapper Gateway")

_graph_channel = grpc.insecure_channel(os.environ.get("GRAPH_SERVICE_ADDR", "localhost:50051"))
_graph_stub = graph_pb2_grpc.GraphServiceStub(_graph_channel)
_qa_channel = grpc.insecure_channel(os.environ.get("QA_SERVICE_ADDR", "localhost:50052"))
_qa_stub = qa_pb2_grpc.QAServiceStub(_qa_channel)


class CallersQuery(BaseModel):
    repo_path: str
    function_id: str


class AskQuery(BaseModel):
    repo_path: str
    function_id: str
    question: str
    model: str = "gemini-3.6-flash"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/callers")
def get_callers(q: CallersQuery):
    try:
        built = _graph_stub.BuildGraph(graph_pb2.BuildGraphRequest(repo_path=q.repo_path))
        resp = _graph_stub.GetCallers(
            graph_pb2.CallersRequest(graph_id=built.graph_id, function_id=q.function_id))
    except grpc.RpcError as e:
        raise HTTPException(status_code=502, detail=f"graph-service error: {e.details()}")
    return {"function_id": q.function_id, "callers": list(resp.caller_ids)}


@app.post("/ask")
def ask(q: AskQuery):
    try:
        resp = _qa_stub.Ask(qa_pb2.AskRequest(
            repo_path=q.repo_path, function_id=q.function_id, question=q.question, model=q.model))
    except grpc.RpcError as e:
        raise HTTPException(status_code=502, detail=f"qa-service error: {e.details()}")
    return {"function_id": q.function_id, "question": q.question, "answer": resp.answer}
