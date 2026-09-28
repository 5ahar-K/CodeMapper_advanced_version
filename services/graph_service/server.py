"""graph-service: a gRPC "face" on codemapper's existing Index/Resolver/graph code.

All the real analysis logic still lives in codemapper/. This file does only three things:
  1. receives a request that arrived over the network
  2. calls the SAME Python functions I already tested (Index.build, build_call_graph, ...)
  3. packs the answer into the message shapes defined in graph_service.proto, and sends it back

Run with:   python -m services.graph_service.server
It will print "listening on port 50051" and then just sit there waiting for requests.
Leave that terminal open. Press Ctrl+C to stop.
"""
from __future__ import annotations

import sys
import uuid
from concurrent import futures
from pathlib import Path

import grpc

# So this file can find the `codemapper` package no matter which folder you run it from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# The generated _grpc.py file imports its sibling with a plain `import graph_service_pb2`
# (not a relative import), so that folder needs to be on sys.path too.
sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))

from codemapper import Index, Resolver, build_call_graph, trusted
from codemapper.graph import blast_radius as compute_blast_radius

import graph_service_pb2 as pb2
import graph_service_pb2_grpc as pb2_grpc


class _GraphHandle:
    """One graph that has been built, kept in memory. If you restart the server, it's gone -
    that's fine for now; a real deployment would save this in a database instead."""
    def __init__(self, index, graph):
        self.index = index
        self.graph = graph


class GraphServiceServicer(pb2_grpc.GraphServiceServicer):
    """This class's method names MUST match the rpc names in the .proto file exactly.
    grpc calls these methods for you whenever a request for that RPC arrives."""

    def __init__(self):
        self._graphs = {}   # graph_id -> _GraphHandle

    def BuildGraph(self, request, context):
        print(f"BuildGraph called with repo_path={request.repo_path}")
        index = Index.build(request.repo_path)            # <- your existing code, unchanged
        graph = build_call_graph(index, Resolver(index))   # <- your existing code, unchanged
        graph_id = str(uuid.uuid4())                       # a random unique id
        self._graphs[graph_id] = _GraphHandle(index, graph)
        return pb2.BuildGraphResponse(graph_id=graph_id, function_count=len(index.functions))

    def GetCallers(self, request, context):
        handle = self._graphs.get(request.graph_id)
        if handle is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown graph_id {request.graph_id!r}; call BuildGraph first")
        view = trusted(handle.graph)                       # hides "ambiguous" edges by default
        callers = list(view.predecessors(request.function_id))
        return pb2.CallersResponse(caller_ids=callers)

    def GetBlastRadius(self, request, context):
        handle = self._graphs.get(request.graph_id)
        if handle is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown graph_id {request.graph_id!r}; call BuildGraph first")
        score = compute_blast_radius(handle.graph, request.function_id)
        return pb2.BlastRadiusResponse(score=score)


def serve(port: int = 50051):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_GraphServiceServicer_to_server(GraphServiceServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"graph-service listening on port {port}  (Ctrl+C to stop)")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()