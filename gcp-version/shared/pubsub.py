"""
shared/pubsub.py
────────────────
Thin async wrapper around the synchronous google-cloud-pubsub client.

Why async wrappers?
  The google-cloud-pubsub library is synchronous (built on gRPC).
  FastAPI runs on an asyncio event loop. Calling blocking I/O directly
  on the event loop would freeze ALL other requests.
  asyncio.to_thread() runs the blocking call in a threadpool so the
  event loop stays free.

Local dev:
  Set PUBSUB_EMULATOR_HOST=localhost:8085 and the library auto-connects
  to the local emulator instead of real GCP. No code change required.

Production:
  Unset PUBSUB_EMULATOR_HOST. Authenticate via:
    - Application Default Credentials (ADC) on Cloud Run (automatic)
    - GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json elsewhere
"""
import asyncio
import json
import logging
import os

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)

PROJECT_ID      = os.environ["GCP_PROJECT_ID"]
TOPIC_ID        = os.environ.get("PUBSUB_TOPIC_ID", "chat-events")
SUBSCRIPTION_ID = os.environ.get("PUBSUB_SUBSCRIPTION_ID", "chat-events-sub")

# Module-level singletons — created once per process.
# Both PublisherClient and SubscriberClient are thread-safe.
_publisher:  pubsub_v1.PublisherClient  | None = None
_subscriber: pubsub_v1.SubscriberClient | None = None


# ── Client accessors ──────────────────────────────────────────────────────────

def get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def get_subscriber() -> pubsub_v1.SubscriberClient:
    global _subscriber
    if _subscriber is None:
        _subscriber = pubsub_v1.SubscriberClient()
    return _subscriber


def _topic_path() -> str:
    return get_publisher().topic_path(PROJECT_ID, TOPIC_ID)


def _subscription_path() -> str:
    return get_subscriber().subscription_path(PROJECT_ID, SUBSCRIPTION_ID)


# ── Infrastructure bootstrap (run once at deploy / startup) ──────────────────

def ensure_topic_and_subscription() -> None:
    """
    Idempotently create the Pub/Sub topic and pull subscription.
    Safe to call on every service startup — AlreadyExists is silently ignored.
    """
    publisher  = get_publisher()
    subscriber = get_subscriber()
    topic_path = _topic_path()
    sub_path   = _subscription_path()

    # Topic
    try:
        publisher.create_topic(request={"name": topic_path})
        logger.info("Pub/Sub topic created: %s", topic_path)
    except AlreadyExists:
        logger.debug("Pub/Sub topic already exists: %s", topic_path)

    # Pull subscription (chat_service pulls; agent_service only publishes)
    try:
        subscriber.create_subscription(request={
            "name": sub_path,
            "topic": topic_path,
            "ack_deadline_seconds": 60,  # consumer has 60 s to ack before redelivery
        })
        logger.info("Pub/Sub subscription created: %s", sub_path)
    except AlreadyExists:
        logger.debug("Pub/Sub subscription already exists: %s", sub_path)


# ── Publisher ─────────────────────────────────────────────────────────────────

async def publish_event(event: dict) -> str:
    """
    Serialize *event* to JSON and publish it to the Pub/Sub topic.
    Returns the server-assigned message_id string.

    Uses asyncio.to_thread so the blocking future.result() call does not
    stall the event loop.
    """
    data      = json.dumps(event).encode("utf-8")
    publisher = get_publisher()
    path      = _topic_path()

    # publisher.publish() returns a concurrent.futures.Future, not asyncio.Future
    future   = publisher.publish(path, data)
    msg_id   = await asyncio.to_thread(future.result)
    logger.debug("Published event type=%s msg_id=%s", event.get("type"), msg_id)
    return msg_id


# ── Subscriber (pull) ─────────────────────────────────────────────────────────

async def pull_messages(max_messages: int = 20) -> list[tuple[str, dict]]:
    """
    Synchronously pull up to *max_messages* from the subscription.
    Returns a list of (ack_id, event_dict) tuples.

    If the subscription is empty the call blocks for up to *timeout* seconds
    then returns an empty list. The consumer loop calls this repeatedly.
    """
    subscriber = get_subscriber()
    path       = _subscription_path()

    try:
        response = await asyncio.to_thread(
            subscriber.pull,
            request={"subscription": path, "max_messages": max_messages},
            timeout=5.0,   # wait up to 5 s for messages before returning empty
        )
    except Exception as exc:
        # DeadlineExceeded is normal when the topic is idle — treat as empty.
        logger.debug("pull_messages timeout/error: %s", exc)
        return []

    results: list[tuple[str, dict]] = []
    for received_msg in response.received_messages:
        try:
            event = json.loads(received_msg.message.data.decode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to decode Pub/Sub message: %s", exc)
            event = {}
        results.append((received_msg.ack_id, event))

    return results


async def acknowledge(ack_ids: list[str]) -> None:
    """Acknowledge a batch of messages so Pub/Sub won't redeliver them."""
    if not ack_ids:
        return
    subscriber = get_subscriber()
    path       = _subscription_path()
    await asyncio.to_thread(
        subscriber.acknowledge,
        request={"subscription": path, "ack_ids": ack_ids},
    )
    logger.debug("Acknowledged %d messages", len(ack_ids))
