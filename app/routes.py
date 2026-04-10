from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.dependencies import get_db_session
from app.schemas import (
    WebhookAttemptResponse,
    WebhookCreateRequest,
    WebhookDetailsResponse,
    WebhookSubmissionResponse,
)
from app.services.webhook_service import (
    create_webhook_request,
    deserialize_payload,
    get_webhook_request,
    list_webhook_attempts,
    list_webhook_requests,
)

router = APIRouter()


@router.get("/")
def healthcheck():
    return {"status": "ok"}


@router.post(
    "/webhooks",
    response_model=WebhookSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_webhook(
    request: WebhookCreateRequest,
    session: Session = Depends(get_db_session),
):
    result = create_webhook_request(session=session, request_data=request)
    return WebhookSubmissionResponse(
        id=result.webhook.id,
        target_url=result.webhook.target_url,
        status=result.webhook.status,
        deduplicated=result.deduplicated,
        created_at=result.webhook.created_at,
    )


@router.get("/webhooks", response_model=list[WebhookDetailsResponse])
def get_webhooks(session: Session = Depends(get_db_session)):
    webhooks = list_webhook_requests(session=session)
    return [
        WebhookDetailsResponse(
            id=webhook.id,
            target_url=webhook.target_url,
            payload=deserialize_payload(webhook.payload_json),
            status=webhook.status,
            attempt_count=webhook.attempt_count,
            max_attempts=webhook.max_attempts,
            next_attempt_at=webhook.next_attempt_at,
            last_error=webhook.last_error,
            created_at=webhook.created_at,
            updated_at=webhook.updated_at,
            delivered_at=webhook.delivered_at,
        )
        for webhook in webhooks
    ]


@router.get("/webhooks/{webhook_id}", response_model=WebhookDetailsResponse)
def get_webhook(webhook_id: int, session: Session = Depends(get_db_session)):
    webhook = get_webhook_request(session=session, webhook_id=webhook_id)
    return WebhookDetailsResponse(
        id=webhook.id,
        target_url=webhook.target_url,
        payload=deserialize_payload(webhook.payload_json),
        status=webhook.status,
        attempt_count=webhook.attempt_count,
        max_attempts=webhook.max_attempts,
        next_attempt_at=webhook.next_attempt_at,
        last_error=webhook.last_error,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        delivered_at=webhook.delivered_at,
    )


@router.get("/webhooks/{webhook_id}/attempts", response_model=list[WebhookAttemptResponse])
def get_webhook_attempts(webhook_id: int, session: Session = Depends(get_db_session)):
    attempts = list_webhook_attempts(session=session, webhook_id=webhook_id)
    return [
        WebhookAttemptResponse(
            id=attempt.id,
            webhook_id=attempt.webhook_id,
            attempt_number=attempt.attempt_number,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            outcome=attempt.outcome,
            response_status=attempt.response_status,
            response_body_excerpt=attempt.response_body_excerpt,
            error_message=attempt.error_message,
        )
        for attempt in attempts
    ]
