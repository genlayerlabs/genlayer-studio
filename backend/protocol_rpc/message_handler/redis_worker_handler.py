"""
Message handler for consensus workers that uses Redis pub/sub for horizontal scaling.
Workers publish events to Redis channels, which are consumed by all RPC instances.
"""

import os
import json
import asyncio
from typing import Optional
import redis.asyncio as aioredis
from loguru import logger

from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.message_handler.types import LogEvent
from backend.protocol_rpc.configuration import GlobalConfiguration


class RedisWorkerMessageHandler(MessageHandler):
    """
    A MessageHandler implementation for consensus workers using Redis pub/sub.
    Enables horizontal scaling by broadcasting events to all RPC instances.
    """

    # Redis channels for different event types
    CONSENSUS_CHANNEL = "consensus:events"
    TRANSACTION_CHANNEL = "transaction:events"
    GENERAL_CHANNEL = "general:events"

    def __init__(
        self,
        config: GlobalConfiguration = None,
        worker_id: Optional[str] = None,
        redis_url: Optional[str] = None,
    ):
        """
        Initialize the worker message handler with Redis.

        Args:
            config: Global configuration object
            worker_id: Unique identifier for this worker
            redis_url: Redis connection URL for pub/sub
        """
        # Initialize parent with None for socketio
        super().__init__(None, config or GlobalConfiguration())

        # Worker identification
        self.worker_id = worker_id or f"worker-{os.getpid()}"

        # Redis configuration
        self.redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://redis:6379/0"
        )
        self.redis_client: Optional[aioredis.Redis] = None

        logger.info(f"Worker {self.worker_id} initialized with Redis pub/sub")

    async def initialize(self):
        """Initialize Redis connection."""
        if not self.redis_client:
            try:
                self.redis_client = await aioredis.from_url(
                    self.redis_url, encoding="utf-8", decode_responses=True
                )
                # Test connection
                await self.redis_client.ping()
                logger.info(
                    f"Worker {self.worker_id} connected to Redis at {self.redis_url}"
                )
            except Exception as e:
                logger.error(f"Worker {self.worker_id} failed to connect to Redis: {e}")
                raise

    def _get_channel_for_event(self, log_event: LogEvent) -> str:
        """
        Determine the appropriate Redis channel for an event.

        Args:
            log_event: The event to route

        Returns:
            The Redis channel name
        """
        if log_event.scope.value == "TRANSACTION":
            return self.TRANSACTION_CHANNEL
        elif log_event.scope.value == "CONSENSUS":
            return self.CONSENSUS_CHANNEL
        else:
            return self.GENERAL_CHANNEL

    async def _publish_to_redis(self, log_event: LogEvent):
        """
        Publish event to Redis pub/sub channel.

        Args:
            log_event: The event to publish
        """
        if not self.redis_client:
            await self.initialize()

        try:
            # Determine channel
            channel = self._get_channel_for_event(log_event)

            # Prepare message
            message = json.dumps(
                {
                    "worker_id": self.worker_id,
                    "event": log_event.name,
                    "data": log_event.to_dict(),
                    "transaction_hash": log_event.transaction_hash,
                }
            )

            # Publish to Redis channel
            subscribers = await self.redis_client.publish(channel, message)

            logger.debug(
                f"Worker {self.worker_id} published {log_event.name} "
                f"to {subscribers} subscribers on {channel}"
            )

        except Exception as e:
            logger.error(f"Failed to publish to Redis: {e}")
            raise

    def _socket_emit(self, log_event: LogEvent):
        """
        Override socket emit to publish events to Redis.

        Args:
            log_event: The event to emit
        """
        # Try to get the current event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the async send operation
                asyncio.create_task(self._publish_to_redis(log_event))
            else:
                # If no loop is running, run it synchronously
                asyncio.run(self._publish_to_redis(log_event))
        except RuntimeError:
            # No event loop available
            logger.error(
                f"No event loop available for worker {self.worker_id}, "
                f"cannot publish event: {log_event.name}"
            )

    def send_message(self, log_event: LogEvent, log_to_terminal: bool = True):
        """
        Send a message (log and publish to Redis).

        Args:
            log_event: The event to log and send
            log_to_terminal: Whether to log to terminal (default True)
        """
        if log_to_terminal:
            self._log_message(log_event)

        # Publish to Redis if it's an important event
        if log_event.transaction_hash or log_event.scope.value in [
            "TRANSACTION",
            "CONSENSUS",
        ]:
            self._socket_emit(log_event)

    async def send_message_async(
        self, log_event: LogEvent, log_to_terminal: bool = True
    ):
        """
        Send a message asynchronously and await Redis publish completion.
        Use this when you need to ensure the message is published before continuing.

        Args:
            log_event: The event to log and send
            log_to_terminal: Whether to log to terminal (default True)
        """
        if log_to_terminal:
            self._log_message(log_event)

        # Publish to Redis if it's an important event and await completion
        if log_event.transaction_hash or log_event.scope.value in [
            "TRANSACTION",
            "CONSENSUS",
        ]:
            await self._publish_to_redis(log_event)

    async def close(self):
        """Clean up resources."""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
            logger.info(f"Worker {self.worker_id} disconnected from Redis")

    async def health_check(self) -> dict:
        """
        Check the health of the Redis connection.

        Returns:
            Dictionary with health status
        """
        health = {
            "worker_id": self.worker_id,
            "redis_url": self.redis_url,
            "redis_connected": False,
        }

        if self.redis_client:
            try:
                await self.redis_client.ping()
                health["redis_connected"] = True

                # Get additional Redis info
                info = await self.redis_client.info()
                health["redis_version"] = info.get("redis_version", "unknown")
                health["connected_clients"] = info.get("connected_clients", 0)
            except Exception as e:
                health["redis_error"] = str(e)

        return health
