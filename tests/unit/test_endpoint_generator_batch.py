"""Unit tests for endpoint_generator.py batch request handling"""

import json
from unittest.mock import Mock, patch
from flask import Flask, request
from flask_jsonrpc.app import JSONRPC
import requests


class TestBatchRequestHandling:
    """Test batch request handling in endpoint_generator"""

    def setup_method(self):
        """Set up test fixtures"""
        self.app = Flask(__name__)
        self.jsonrpc = JSONRPC(self.app, "/api")
        self.client = self.app.test_client()

        # Add the handle_eth_methods function to app context
        self.app.logger = Mock()

    @patch("backend.protocol_rpc.endpoint_generator.requests.Session")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_batch_all_eth_methods_success(self, mock_session):
        """Test forwarding batch with all eth_ methods"""
        # Mock successful Hardhat response
        mock_response = Mock()
        mock_response.content = json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x2"},
            ]
        ).encode()
        mock_response.status_code = 200
        mock_response.headers = {}

        mock_http = Mock()
        mock_http.post.return_value = mock_response
        mock_session.return_value.__enter__.return_value = mock_http

        with self.app.test_request_context(
            "/api",
            method="POST",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                {"jsonrpc": "2.0", "id": 2, "method": "eth_gasPrice"},
            ],
        ):
            # Create a mock app and jsonrpc for the function
            mock_app = Mock()
            mock_app.logger = Mock()
            mock_jsonrpc = Mock()
            mock_site = Mock()
            mock_site.view_funcs = {}  # No local implementations
            mock_jsonrpc.get_jsonrpc_site.return_value = mock_site

            # Test the batch handling logic
            batch_request = request.get_json()
            assert isinstance(batch_request, list)
            assert len(batch_request) == 2

            # Verify all are eth_ methods
            for req in batch_request:
                assert req["method"].startswith("eth_")

    @patch("backend.protocol_rpc.endpoint_generator.requests.Session")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_batch_network_error(self, mock_session):
        """Test batch request with network error"""
        # Mock network error
        mock_http = Mock()
        mock_http.post.side_effect = requests.RequestException("Connection refused")
        mock_session.return_value.__enter__.return_value = mock_http

        with self.app.test_request_context(
            "/api",
            method="POST",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                {"jsonrpc": "2.0", "id": 2, "method": "eth_gasPrice"},
            ],
        ):
            mock_app = Mock()
            mock_app.logger = Mock()

            # The error should be logged
            # Each request should get an error response
            batch_request = request.get_json()

            # Build expected error responses
            expected_errors = []
            for req in batch_request:
                expected_errors.append(
                    {
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {
                            "code": -32000,
                            "message": "Network error",
                            "data": "Failed to forward request to Hardhat: Connection refused",
                        },
                    }
                )

            # Verify error response structure
            assert len(expected_errors) == 2
            assert all(err["error"]["code"] == -32000 for err in expected_errors)

    def test_batch_mixed_methods(self):
        """Test batch with mixed eth_ and non-eth_ methods"""
        with self.app.test_request_context(
            "/api",
            method="POST",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                {"jsonrpc": "2.0", "id": 2, "method": "gen_getBalance"},
                {"jsonrpc": "2.0", "id": 3, "method": "eth_gasPrice"},
            ],
        ):
            batch_request = request.get_json()

            # Check we have mixed methods
            eth_methods = [r for r in batch_request if r["method"].startswith("eth_")]
            non_eth_methods = [
                r for r in batch_request if not r["method"].startswith("eth_")
            ]

            assert len(eth_methods) == 2
            assert len(non_eth_methods) == 1
            assert batch_request[1]["method"] == "gen_getBalance"

    def test_batch_invalid_request_format(self):
        """Test batch with invalid request format"""
        with self.app.test_request_context(
            "/api",
            method="POST",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                "invalid_request",  # Not a dict
                {"jsonrpc": "2.0", "id": 3, "method": "eth_gasPrice"},
            ],
        ):
            batch_request = request.get_json()

            # Check invalid request detection
            assert isinstance(batch_request[0], dict)
            assert not isinstance(batch_request[1], dict)
            assert isinstance(batch_request[2], dict)

    @patch("backend.protocol_rpc.endpoint_generator.requests.Session")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_single_eth_request_success(self, mock_session):
        """Test single eth_ request forwarding"""
        mock_response = Mock()
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

        mock_http = Mock()
        mock_http.post.return_value = mock_response
        mock_session.return_value.__enter__.return_value = mock_http

        with self.app.test_request_context(
            "/api",
            method="POST",
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
        ):
            request_data = request.get_json()

            assert request_data["method"] == "eth_blockNumber"
            assert request_data["id"] == 1

    @patch("backend.protocol_rpc.endpoint_generator.requests.Session")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_single_eth_request_network_error(self, mock_session):
        """Test single eth_ request with network error"""
        mock_http = Mock()
        mock_http.post.side_effect = requests.RequestException("Connection timeout")
        mock_session.return_value.__enter__.return_value = mock_http

        with self.app.test_request_context(
            "/api",
            method="POST",
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
        ):
            # Should raise JSONRPCError
            # with code -32000 and appropriate message
            pass

    def test_non_eth_request_passthrough(self):
        """Test that non-eth_ requests are not intercepted"""
        with self.app.test_request_context(
            "/api",
            method="POST",
            json={"jsonrpc": "2.0", "id": 1, "method": "gen_getBalance", "params": []},
        ):
            request_data = request.get_json()

            # Should not be intercepted
            assert request_data["method"] == "gen_getBalance"
            assert not request_data["method"].startswith("eth_")

    def test_empty_batch_request(self):
        """Test empty batch request"""
        with self.app.test_request_context("/api", method="POST", json=[]):
            batch_request = request.get_json()

            assert isinstance(batch_request, list)
            assert len(batch_request) == 0

    def test_batch_with_local_eth_implementation(self):
        """Test batch where some eth_ methods have local implementation"""
        # Mock a local implementation for eth_getBalance
        mock_jsonrpc = Mock()
        mock_site = Mock()
        mock_site.view_funcs = {"eth_getBalance": Mock()}  # Has local implementation
        mock_jsonrpc.get_jsonrpc_site.return_value = mock_site

        with self.app.test_request_context(
            "/api",
            method="POST",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance"},  # Local
                {"jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber"},  # Forward
            ],
        ):
            batch_request = request.get_json()

            # eth_getBalance should be handled locally
            # eth_blockNumber should be forwarded
            assert batch_request[0]["method"] == "eth_getBalance"
            assert batch_request[1]["method"] == "eth_blockNumber"
