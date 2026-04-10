from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, JsonValue

from app.models import AttemptOutcome, WebhookStatus


class WebhookCreateRequest(BaseModel):
    target_url: HttpUrl
    payload: JsonValue


class WebhookSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_url: str
    status: WebhookStatus
    deduplicated: bool
    created_at: datetime


class WebhookDetailsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_url: str
    payload: JsonValue
    status: WebhookStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


class WebhookAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    webhook_id: int
    attempt_number: int
    started_at: datetime
    finished_at: datetime | None
    outcome: AttemptOutcome
    response_status: int | None
    response_body_excerpt: str | None
    error_message: str | None
