"""A client that asks qa-service a question. qa-service will internally call graph-service
for you (you will see BOTH server terminals print something when you run this).
"""
import sys
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))
import qa_service_pb2 as pb2
import qa_service_pb2_grpc as pb2_grpc

channel = grpc.insecure_channel("localhost:50052")
stub = pb2_grpc.QAServiceStub(channel)

request = pb2.AskRequest(
    repo_path="C:\\tmp\\click_repo",
    function_id="src/click/core.py::Context.invoke",
    question="what does this function do?",
    model="gemini-3.6-flash",
)

response = stub.Ask(request)
print("Answer:")
print(response.answer)
