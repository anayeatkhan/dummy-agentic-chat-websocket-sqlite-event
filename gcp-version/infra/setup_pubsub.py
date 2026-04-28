#!/usr/bin/env python3
"""
infra/setup_pubsub.py
─────────────────────
One-time script to create the GCP Pub/Sub topic and subscription.
Safe to re-run — AlreadyExists errors are ignored.

Usage:
    export GCP_PROJECT_ID=your-project-id
    python infra/setup_pubsub.py

Or with the emulator for local dev:
    export GCP_PROJECT_ID=local-project
    export PUBSUB_EMULATOR_HOST=localhost:8085
    python infra/setup_pubsub.py
"""
import os
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

PROJECT_ID      = os.environ.get("GCP_PROJECT_ID")
TOPIC_ID        = os.environ.get("PUBSUB_TOPIC_ID", "chat-events")
SUBSCRIPTION_ID = os.environ.get("PUBSUB_SUBSCRIPTION_ID", "chat-events-sub")

if not PROJECT_ID:
    print("ERROR: GCP_PROJECT_ID environment variable is not set.", file=sys.stderr)
    sys.exit(1)

publisher  = pubsub_v1.PublisherClient()
subscriber = pubsub_v1.SubscriberClient()

topic_path        = publisher.topic_path(PROJECT_ID, TOPIC_ID)
subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)


def create_topic() -> None:
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"[OK] Topic created:       {topic_path}")
    except AlreadyExists:
        print(f"[--] Topic already exists: {topic_path}")


def create_subscription() -> None:
    try:
        subscriber.create_subscription(request={
            "name":                 subscription_path,
            "topic":                topic_path,
            "ack_deadline_seconds": 60,   # consumer has 60 s to ack before redelivery
        })
        print(f"[OK] Subscription created:       {subscription_path}")
    except AlreadyExists:
        print(f"[--] Subscription already exists: {subscription_path}")


if __name__ == "__main__":
    print(f"\nProject:      {PROJECT_ID}")
    print(f"Topic:        {TOPIC_ID}")
    print(f"Subscription: {SUBSCRIPTION_ID}")
    emulator = os.environ.get("PUBSUB_EMULATOR_HOST")
    if emulator:
        print(f"Emulator:     {emulator}\n")
    else:
        print("Emulator:     (none — using real GCP)\n")

    create_topic()
    create_subscription()
    print("\nDone.")
