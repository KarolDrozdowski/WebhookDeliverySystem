from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WebhookAttempt, WebhookRequest, utc_now
from app.schemas import WebhookCreateRequest


DEDUPLICATION_WINDOW_SECONDS = 10


def serialize_payload(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_payload(serialized_payload: str) -> str:
    return hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()


def deserialize_payload(payload_json: str) -> object:
    return json.loads(payload_json)


@dataclass(frozen=True)
class SubmissionResult:
    webhook: WebhookRequest
    deduplicated: bool


def create_webhook_request(
    session: Session,
    request_data: WebhookCreateRequest,
) -> SubmissionResult:
    serialized_payload = serialize_payload(request_data.payload)
    payload_hash = hash_payload(serialized_payload)
    deduplication_threshold = utc_now() - timedelta(seconds=DEDUPLICATION_WINDOW_SECONDS)

    existing_webhook = session.scalar(
        select(WebhookRequest)
        .where(WebhookRequest.target_url == str(request_data.target_url))
        .where(WebhookRequest.payload_hash == payload_hash)
        .where(WebhookRequest.created_at >= deduplication_threshold)
        .order_by(WebhookRequest.created_at.desc())
    )
    if existing_webhook is not None:
        return SubmissionResult(webhook=existing_webhook, deduplicated=True)

    webhook = WebhookRequest(
        target_url=str(request_data.target_url),
        payload_json=serialized_payload,
        payload_hash=payload_hash,
    )
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    return SubmissionResult(webhook=webhook, deduplicated=False)


def list_webhook_requests(session: Session) -> list[WebhookRequest]:
    return list(
        session.scalars(
            select(WebhookRequest).order_by(WebhookRequest.created_at.desc(), WebhookRequest.id.desc())
        )
    )


def get_webhook_request(session: Session, webhook_id: int) -> WebhookRequest:
    webhook = session.get(WebhookRequest, webhook_id)
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook with id={webhook_id} was not found.",
        )
    return webhook


def list_webhook_attempts(session: Session, webhook_id: int) -> list[WebhookAttempt]:
    get_webhook_request(session=session, webhook_id=webhook_id)
    return list(
        session.scalars(
            select(WebhookAttempt)
            .where(WebhookAttempt.webhook_id == webhook_id)
            .order_by(WebhookAttempt.attempt_number.asc(), WebhookAttempt.id.asc())
        )
    )
