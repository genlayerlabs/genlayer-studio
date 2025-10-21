"""
Message handler for consensus workers that forwards events to JSON-RPC server.
Workers use this to send events via HTTP to the central JSON-RPC server,
which then broadcasts them to subscribed WebSocket clients.
"""

import os
import json
import asyncio
from typing import Optional
import aiohttp
from loguru import logger

from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.message_handler.types import LogEvent
from backend.protocol_rpc.configuration import GlobalConfiguration


class WorkerMessageHandler(MessageHandler):
    """
    A MessageHandler implementation for consensus workers.
    Workers send events to the JSON-RPC server via HTTP POST,
    which then forwards them to WebSocket clients.
    """
    
    def __init__(
        self, 
        config: GlobalConfiguration = None,
        jsonrpc_url: Optional[str] = None,
        worker_id: Optional[str] = None
    ):
        """
        Initialize the worker message handler.
        
        Args:
            config: Global configuration object
            jsonrpc_url: URL of the JSON-RPC server's internal event endpoint
            worker_id: Unique identifier for this worker
        """
        # Initialize parent with None for socketio
        super().__init__(None, config or GlobalConfiguration())
        
        # Get JSON-RPC server URL from parameter or environment
        self.jsonrpc_url = jsonrpc_url or os.environ.get(
            "JSONRPC_SERVER_URL", 
            "http://localhost:4000"
        )
        self.internal_events_endpoint = f"{self.jsonrpc_url}/internal/events"
        self.worker_id = worker_id or "unknown"
        
        # Create a session for HTTP requests
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def _send_event_to_server(self, log_event: LogEvent):
        """
        Send an event to the JSON-RPC server via HTTP POST.
        
        Args:
            log_event: The event to send
        """
        try:
            session = await self._get_session()
            
            # Prepare the event data
            event_data = {
                "worker_id": self.worker_id,
                "event": log_event.name,
                "data": log_event.to_dict(),
                "transaction_hash": log_event.transaction_hash,
            }
            
            # Add authentication if configured
            headers = {
                "Content-Type": "application/json"
            }
            
            # Add shared secret if configured
            internal_secret = os.environ.get("INTERNAL_EVENT_SECRET")
            if internal_secret:
                headers["X-Internal-Secret"] = internal_secret
            
            # Send the event to the JSON-RPC server
            async with session.post(
                self.internal_events_endpoint,
                json=event_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status != 200:
                    response_text = await response.text()
                    logger.warning(
                        f"Failed to send event to JSON-RPC server: "
                        f"status={response.status}, response={response_text}"
                    )
                    
        except asyncio.TimeoutError:
            logger.warning(f"Timeout sending event to JSON-RPC server: {log_event.name}")
        except Exception as e:
            logger.error(f"Error sending event to JSON-RPC server: {e}")
    
    def _socket_emit(self, log_event: LogEvent):
        """
        Override socket emit to send events to JSON-RPC server.
        
        Args:
            log_event: The event to emit
        """
        # Try to get the current event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the async send operation
                asyncio.create_task(self._send_event_to_server(log_event))
            else:
                # If no loop is running, try to run it synchronously
                asyncio.run(self._send_event_to_server(log_event))
        except RuntimeError:
            # No event loop available, log the event locally only
            logger.debug(
                f"No event loop available for worker {self.worker_id}, "
                f"event not sent to server: {log_event.name}"
            )
    
    def send_message(self, log_event: LogEvent, log_to_terminal: bool = True):
        """
        Send a message (log and forward to JSON-RPC server).
        
        Args:
            log_event: The event to log and send
            log_to_terminal: Whether to log to terminal (default True)
        """
        if log_to_terminal:
            self._log_message(log_event)
        
        # Forward to JSON-RPC server if it's an important event
        if log_event.transaction_hash or log_event.scope.value in ["TRANSACTION", "CONSENSUS"]:
            self._socket_emit(log_event)
    
    async def close(self):
        """Clean up resources."""
        if self._session:
            await self._session.close()
            self._session = None