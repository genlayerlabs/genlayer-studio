"""
Redis subscriber for RPC instances to receive events from consensus workers.
Integrates with Broadcast to forward events to WebSocket clients.
"""

import os
import json
import asyncio
from typing import Optional, Callable, Dict, Any
import redis.asyncio as aioredis
from loguru import logger

from backend.protocol_rpc.broadcast import Broadcast


class RedisEventSubscriber:
    """
    Subscribes to Redis channels and forwards events to WebSocket clients via Broadcast.
    Each RPC instance runs its own subscriber to receive all worker events.
    """

    # Redis channels to subscribe to
    CHANNELS = ["consensus:events", "transaction:events", "general:events"]

    def __init__(
        self,
        redis_url: Optional[str] = None,
        broadcast: Optional[Broadcast] = None,
        instance_id: Optional[str] = None,
    ):
        """
        Initialize the Redis subscriber.

        Args:
            redis_url: Redis connection URL
            broadcast: Broadcast instance for WebSocket fan-out
            instance_id: Unique identifier for this RPC instance
        """
        self.redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://redis:6379/0"
        )
        self.broadcast = broadcast
        self.instance_id = instance_id or f"rpc-{os.getpid()}"

        self.redis_client: Optional[aioredis.Redis] = None
        self.pubsub: Optional[aioredis.client.PubSub] = None
        self.subscription_task: Optional[asyncio.Task] = None
        self.is_running = False

        # Event handlers registry
        self.event_handlers: Dict[str, Callable] = {}

        logger.info(f"RPC instance {self.instance_id} Redis subscriber initialized")

    async def connect(self):
        """Connect to Redis and set up pub/sub."""
        try:
            self.redis_client = aioredis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=True
            )

            # Test connection
            await self.redis_client.ping()

            # Create pub/sub instance
            self.pubsub = self.redis_client.pubsub()

            # Subscribe to channels
            await self.pubsub.subscribe(*self.CHANNELS)

            logger.info(
                f"RPC instance {self.instance_id} connected to Redis "
                f"and subscribed to channels: {self.CHANNELS}"
            )

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    async def start(self):
        """Start listening for events."""
        if self.is_running:
            logger.warning(f"Subscriber for {self.instance_id} is already running")
            return

        if not self.redis_client:
            await self.connect()

        self.is_running = True
        self.subscription_task = asyncio.create_task(self._listen_for_events())
        logger.info(
            f"RPC instance {self.instance_id} started listening for Redis events"
        )

    async def stop(self):
        """Stop listening and clean up."""
        self.is_running = False

        if self.subscription_task:
            self.subscription_task.cancel()
            try:
                await self.subscription_task
            except asyncio.CancelledError:
                pass
            self.subscription_task = None

        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
            self.pubsub = None

        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None

        logger.info(f"RPC instance {self.instance_id} stopped Redis subscriber")

    async def _listen_for_events(self):
        """Main loop to listen for events from Redis."""
        logger.info(f"RPC instance {self.instance_id} listening for events...")

        try:
            async for message in self.pubsub.listen():
                if not self.is_running:
                    break

                # Skip subscribe confirmation messages
                if message["type"] != "message":
                    continue

                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

        except asyncio.CancelledError:
            logger.info(f"Event listener for {self.instance_id} cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in event listener: {e}")
            if self.is_running:
                # Clean up before reconnection
                self.is_running = False
                self.subscription_task = None
                # Attempt to reconnect after delay
                await asyncio.sleep(5)
                await self.start()

    async def _process_message(self, message: Dict[str, Any]):
        """
        Process a message received from Redis.

        Args:
            message: Redis pub/sub message
        """
        channel = message["channel"]
        data = message["data"]

        try:
            # Parse JSON message
            event_data = json.loads(data)

            worker_id = event_data.get("worker_id", "unknown")
            event_name = event_data.get("event", "unknown")
            event_payload = event_data.get("data", {})
            transaction_hash = event_data.get("transaction_hash")

            logger.debug(
                f"RPC {self.instance_id} received {event_name} "
                f"from {worker_id} on {channel}"
            )

            # Call registered event handler if exists
            if event_name in self.event_handlers:
                await self.event_handlers[event_name](event_payload)

            # Broadcast to WebSocket clients if broadcast is available
            if self.broadcast:
                await self._broadcast_to_websocket(
                    channel, event_name, event_payload, transaction_hash
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message from {channel}: {e}")
        except Exception as e:
            logger.error(f"Error processing message from {channel}: {e}")

    async def _broadcast_to_websocket(
        self,
        channel: str,
        event: str,
        data: Dict[str, Any],
        transaction_hash: Optional[str] = None,
    ):
        """
        Broadcast event to WebSocket clients via Broadcast channels.

        Args:
            channel: Redis channel the event came from
            event: Event name
            data: Event data
            transaction_hash: Optional transaction hash for channel-based routing
        """
        if not self.broadcast:
            return

        try:
            # Prepare message payload
            message = json.dumps({"event": event, "data": data})

            # Determine broadcast channel based on transaction hash or Redis channel
            if transaction_hash:
                # Send to transaction-specific channel
                await self.broadcast.publish(channel=transaction_hash, message=message)
                logger.debug(
                    f"Published {event} to broadcast channel: {transaction_hash}"
                )
            else:
                # Send to general broadcast channel based on Redis channel
                channel_map = {
                    "consensus:events": "consensus",
                    "transaction:events": "transactions",
                    "general:events": "general",
                }
                broadcast_channel = channel_map.get(channel, "__broadcast__")
                await self.broadcast.publish(channel=broadcast_channel, message=message)
                logger.debug(
                    f"Published {event} to broadcast channel: {broadcast_channel}"
                )

        except Exception as e:
            logger.error(f"Error broadcasting to WebSocket: {e}")

    def register_handler(self, event: str, handler: Callable):
        """
        Register a handler for a specific event type.

        Args:
            event: Event name
            handler: Async function to handle the event
        """
        self.event_handlers[event] = handler
        logger.debug(f"Registered handler for event: {event}")

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the Redis subscriber.

        Returns:
            Dictionary with health status
        """
        health = {
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "is_running": self.is_running,
            "redis_connected": False,
            "subscribed_channels": [],
        }

        if self.redis_client:
            try:
                await self.redis_client.ping()
                health["redis_connected"] = True

                if self.pubsub:
                    health["subscribed_channels"] = self.CHANNELS

            except Exception as e:
                health["error"] = str(e)

        return health

    async def publish_event(self, channel: str, event: str, data: Dict[str, Any]):
        """
        Publish an event to Redis (for RPC-originated events).

        Args:
            channel: Redis channel to publish to
            event: Event name
            data: Event data
        """
        if not self.redis_client:
            logger.error("Cannot publish: Redis client not connected")
            return

        try:
            message = json.dumps(
                {"instance_id": self.instance_id, "event": event, "data": data}
            )

            await self.redis_client.publish(channel, message)
            logger.debug(f"Published {event} to {channel}")

        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
