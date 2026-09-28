"""A client that connects to graph-service and calls its 3 functions.
We run the server first (python -m services.graph_service.server), then run this in a SEPARATE terminal.
"""
import sys
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))
import graph_service_pb2 as pb2
import graph_service_pb2_grpc as pb2_grpc

# Connect to the server (it's running on your own machine, port 50051)
channel = grpc.insecure_channel("localhost:50051")
stub = pb2_grpc.GraphServiceStub(channel)

# 1. Build a graph for a repo
repo_path = "C:\\tmp\\click_repo"
resp = stub.BuildGraph(pb2.BuildGraphRequest(repo_path=repo_path))
print("BuildGraph result:", resp)

graph_id = resp.graph_id

# 2. Ask who calls a specific function
function_id = "src/click/core.py::Context.invoke"
resp2 = stub.GetCallers(pb2.CallersRequest(graph_id=graph_id, function_id=function_id))
print("Callers of Context.invoke:", list(resp2.caller_ids))

# 3. Ask for its blast radius
resp3 = stub.GetBlastRadius(pb2.BlastRadiusRequest(graph_id=graph_id, function_id=function_id))
print("Blast radius:", resp3.score)