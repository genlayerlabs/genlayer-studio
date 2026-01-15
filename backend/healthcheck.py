"""
File used to check the health of the backend API.
We only check that the API is responding, and that the response is valid.
"""

import argparse
import json

import requests

parser = argparse.ArgumentParser(description="Check the health of the backend API.")
parser.add_argument(
    "--port",
    type=str,
    nargs="?",
    help="The port number for the API",
)

args = parser.parse_args()

response = requests.post(
    f"http://localhost:{args.port}/api",
    data=json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "ping",
            "params": [],
            "id": 1,
        }
    ),
    headers={"Content-Type": "application/json"},
)
assert response.status_code == 200
print(response.json())
assert response.json() == {"id": 1, "jsonrpc": "2.0", "result": "OK"}
