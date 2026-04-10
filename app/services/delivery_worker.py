from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.db import SessionLocal
from app.models import AttemptOutcome, WebhookAttempt, WebhookRequest, WebhookStatus, utc_now
from app.services.webhook_service import deserialize_payload


MAX_RESPONSE_BODY_EXCERPT_LENGTH = 500


class DeliveryWorker:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.worker_max_concurrency)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._active_webhook_ids: set[int] = set()
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._client = httpx.AsyncClient(timeout=settings.delivery_timeout_seconds)

    async def start(self) -> None:
        self._reset_interrupted_webhooks()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

        for task in list(self._active_tasks):
            task.cancel()

        for task in list(self._active_tasks):
            with suppress(asyncio.CancelledError):
                await task

        await self._client.aclose()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            available_slots = self._settings.worker_max_concurrency - len(self._active_webhook_ids)
            if available_slots > 0:
                for webhook_id in self._claim_due_webhook_ids(limit=available_slots):
                    self._active_webhook_ids.add(webhook_id)
                    task = asyncio.create_task(self._process_webhook(webhook_id))
                    self._active_tasks.add(task)
                    task.add_done_callback(self._on_task_done)

            await asyncio.sleep(self._settings.worker_poll_interval_seconds)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.discard(task)
        with suppress(asyncio.CancelledError, Exception):
            task.result()

    def _reset_interrupted_webhooks(self) -> None:
        session = SessionLocal()
        try:
            interrupted_webhooks = list(
                session.scalars(
                    select(WebhookRequest).where(WebhookRequest.status == WebhookStatus.PROCESSING)
                )
            )
            for webhook in interrupted_webhooks:
                webhook.status = WebhookStatus.PENDING
                webhook.next_attempt_at = utc_now()
            session.commit()
        finally:
            session.close()

    def _claim_due_webhook_ids(self, limit: int) -> list[int]:
        session = SessionLocal()
        try:
            due_webhooks = list(
                session.scalars(
                    select(WebhookRequest)
                    .where(WebhookRequest.status == WebhookStatus.PENDING)
                    .where(WebhookRequest.next_attempt_at <= utc_now())
                    .order_by(WebhookRequest.created_at.asc(), WebhookRequest.id.asc())
                    .limit(limit)
                )
            )

            claimed_ids: list[int] = []
            for webhook in due_webhooks:
                if webhook.id in self._active_webhook_ids:
                    continue
                webhook.status = WebhookStatus.PROCESSING
                claimed_ids.append(webhook.id)

            session.commit()
            return claimed_ids
        finally:
            session.close()

    async def _process_webhook(self, webhook_id: int) -> None:
        try:
            async with self._semaphore:
                await self._deliver_webhook(webhook_id)
        finally:
            self._active_webhook_ids.discard(webhook_id)

    async def _deliver_webhook(self, webhook_id: int) -> None:
        session = SessionLocal()
        started_at = utc_now()
        try:
            webhook = session.get(WebhookRequest, webhook_id)
            if webhook is None:
                return

            attempt_number = webhook.attempt_count + 1
            payload = deserialize_payload(webhook.payload_json)

            try:
                response = await self._client.post(webhook.target_url, json=payload)
            except httpx.RequestError as exc:
                self._mark_attempt_failure(
                    session=session,
                    webhook=webhook,
                    attempt_number=attempt_number,
                    started_at=started_at,
                    outcome=AttemptOutcome.NETWORK_ERROR,
                    error_message=str(exc),
                )
                return
            except Exception as exc:
                self._mark_attempt_failure(
                    session=session,
                    webhook=webhook,
                    attempt_number=attempt_number,
                    started_at=started_at,
                    outcome=AttemptOutcome.NETWORK_ERROR,
                    error_message=f"Unexpected delivery error: {exc}",
                )
                return

            if 200 <= response.status_code < 300:
                self._mark_attempt_success(
                    session=session,
                    webhook=webhook,
                    attempt_number=attempt_number,
                    started_at=started_at,
                    response=response,
                )
                return

            self._mark_attempt_failure(
                session=session,
                webhook=webhook,
                attempt_number=attempt_number,
                started_at=started_at,
                outcome=AttemptOutcome.HTTP_ERROR,
                error_message=f"Target returned HTTP {response.status_code}",
                response=response,
            )
        finally:
            session.close()

    def _mark_attempt_success(
        self,
        session,
        webhook: WebhookRequest,
        attempt_number: int,
        started_at,
        response: httpx.Response,
    ) -> None:
        finished_at = utc_now()
        webhook.attempt_count = attempt_number
        webhook.status = WebhookStatus.DELIVERED
        webhook.last_error = None
        webhook.delivered_at = finished_at
        webhook.next_attempt_at = finished_at

        session.add(
            WebhookAttempt(
                webhook_id=webhook.id,
                attempt_number=attempt_number,
                started_at=started_at,
                finished_at=finished_at,
                outcome=AttemptOutcome.SUCCESS,
                response_status=response.status_code,
                response_body_excerpt=response.text[:MAX_RESPONSE_BODY_EXCERPT_LENGTH] or None,
                error_message=None,
            )
        )
        session.commit()

    def _mark_attempt_failure(
        self,
        session,
        webhook: WebhookRequest,
        attempt_number: int,
        started_at,
        outcome: AttemptOutcome,
        error_message: str,
        response: httpx.Response | None = None,
    ) -> None:
        finished_at = utc_now()
        webhook.attempt_count = attempt_number
        webhook.last_error = error_message

        if attempt_number >= webhook.max_attempts:
            webhook.status = WebhookStatus.FAILED
            webhook.next_attempt_at = finished_at
        else:
            webhook.status = WebhookStatus.PENDING
            webhook.next_attempt_at = finished_at + timedelta(seconds=self._settings.retry_delay_seconds)

        session.add(
            WebhookAttempt(
                webhook_id=webhook.id,
                attempt_number=attempt_number,
                started_at=started_at,
                finished_at=finished_at,
                outcome=outcome,
                response_status=response.status_code if response is not None else None,
                response_body_excerpt=(
                    response.text[:MAX_RESPONSE_BODY_EXCERPT_LENGTH] if response is not None else None
                ),
                error_message=error_message,
            )
        )
        session.commit()
