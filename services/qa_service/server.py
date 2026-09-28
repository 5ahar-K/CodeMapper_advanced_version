"""qa-service: answers questions about a function using the LLM.

Unusual detail: this service is BOTH a gRPC SERVER (other things call IT to ask a question)
AND a gRPC CLIENT (it calls graph-service to find out who calls the function first, so the
LLM has real context instead of just the bare source code).
"""
from __future__ import annotations

import os
import sys
from concurrent import futures
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "graph_service" / "generated"))

from codemapper import Index
from codemapper.llm import make_gemini

import qa_service_pb2 as pb2
import qa_service_pb2_grpc as pb2_grpc
import graph_service_pb2 as graph_pb2
import graph_service_pb2_grpc as graph_pb2_grpc


class QAServiceServicer(pb2_grpc.QAServiceServicer):
    def __init__(self, graph_service_address: str = None):
        graph_service_address = graph_service_address or os.environ.get("GRAPH_SERVICE_ADDR", "localhost:50051")
        channel = grpc.insecure_channel(graph_service_address)
        self._graph_stub = graph_pb2_grpc.GraphServiceStub(channel)

    def Ask(self, request, context):
        index = Index.build(request.repo_path)
        if request.function_id not in index.functions:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown function_id {request.function_id!r}")
        source = index.source_of(request.function_id)

        built = self._graph_stub.BuildGraph(graph_pb2.BuildGraphRequest(repo_path=request.repo_path))
        callers_resp = self._graph_stub.GetCallers(
            graph_pb2.CallersRequest(graph_id=built.graph_id, function_id=request.function_id))
        callers_text = "\n".join(f"- {c}" for c in callers_resp.caller_ids) or "(no known callers)"

        prompt = (
            f"Function `{request.function_id}`:\n\n```python\n{source}\n```\n\n"
            f"It is called by:\n{callers_text}\n\n"
            f"Question: {request.question}\n\n"
            f"Answer using only the information above."
        )
        llm = make_gemini(request.model)
        answer = llm(prompt)
        return pb2.AskResponse(answer=answer)


def serve(port: int = 50052, graph_service_address: str = None):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_QAServiceServicer_to_server(QAServiceServicer(graph_service_address), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"qa-service listening on port {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
