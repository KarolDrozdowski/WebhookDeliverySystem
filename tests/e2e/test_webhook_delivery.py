from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module

import pytest
from fastapi.testclient import TestClient


def wait_for_status(
    client: TestClient,
    webhook_id: int,
    expected_status: str,
    timeout_seconds: float = 5,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/webhooks/{webhook_id}")
        response.raise_for_status()
        last_payload = response.json()
        if last_payload["status"] == expected_status:
            return last_payload
        time.sleep(0.1)

    raise AssertionError(f"Webhook {webhook_id} did not reach status={expected_status}. Last payload: {last_payload}")


def wait_for_all_delivered(
    client: TestClient,
    webhook_ids: list[int],
    timeout_seconds: float = 15,
) -> list[dict]:
    deadline = time.time() + timeout_seconds
    last_payloads: list[dict] = []
    while time.time() < deadline:
        last_payloads = []
        all_delivered = True
        for webhook_id in webhook_ids:
            response = client.get(f"/webhooks/{webhook_id}")
            response.raise_for_status()
            payload = response.json()
            last_payloads.append(payload)
            if payload["status"] != "delivered":
                all_delivered = False
        if all_delivered:
            return last_payloads
        time.sleep(0.1)

    raise AssertionError(f"Not all webhooks reached delivered status. Last payloads: {last_payloads}")


def test_happy_path_delivers_webhook(app_client: TestClient, webhook_target) -> None:
    response = app_client.post(
        "/webhooks",
        json={"target_url": webhook_target.url, "payload": {"event": "created", "order_id": 1}},
    )

    assert response.status_code == 202
    webhook_id = response.json()["id"]

    details = wait_for_status(app_client, webhook_id=webhook_id, expected_status="delivered")
    attempts = app_client.get(f"/webhooks/{webhook_id}/attempts")
    attempts_payload = attempts.json()

    assert details["attempt_count"] == 1
    assert len(webhook_target.requests) == 1
    assert webhook_target.requests[0].body == '{"event":"created","order_id":1}'
    assert attempts.status_code == 200
    assert len(attempts_payload) == 1
    assert attempts_payload[0]["webhook_id"] == webhook_id
    assert attempts_payload[0]["attempt_number"] == 1
    assert attempts_payload[0]["outcome"] == "success"
    assert attempts_payload[0]["response_status"] == 204
    assert attempts_payload[0]["response_body_excerpt"] is None
    assert attempts_payload[0]["error_message"] is None


def test_retries_and_eventual_success(app_client: TestClient, webhook_target) -> None:
    webhook_target.statuses = [500, 500, 204]
    webhook_target.response_bodies = {500: "status=500"}

    response = app_client.post(
        "/webhooks",
        json={"target_url": webhook_target.url, "payload": {"event": "retry", "order_id": 2}},
    )

    assert response.status_code == 202
    webhook_id = response.json()["id"]

    details = wait_for_status(app_client, webhook_id=webhook_id, expected_status="delivered")
    attempts = app_client.get(f"/webhooks/{webhook_id}/attempts")
    attempts.raise_for_status()
    attempts_payload = attempts.json()

    assert details["attempt_count"] == 3
    assert len(attempts_payload) == 3
    assert [item["outcome"] for item in attempts_payload] == ["http_error", "http_error", "success"]
    assert [item["response_status"] for item in attempts_payload] == [500, 500, 204]
    assert attempts_payload[0]["response_body_excerpt"] == "status=500"
    assert len(webhook_target.requests) == 3


def test_fails_after_three_attempts(app_client: TestClient, webhook_target) -> None:
    webhook_target.statuses = [500]
    webhook_target.response_bodies = {500: "still failing"}

    response = app_client.post(
        "/webhooks",
        json={"target_url": webhook_target.url, "payload": {"event": "fail", "order_id": 3}},
    )

    assert response.status_code == 202
    webhook_id = response.json()["id"]

    details = wait_for_status(app_client, webhook_id=webhook_id, expected_status="failed")
    attempts = app_client.get(f"/webhooks/{webhook_id}/attempts")
    attempts.raise_for_status()
    attempts_payload = attempts.json()

    assert details["attempt_count"] == 3
    assert details["last_error"] == "Target returned HTTP 500"
    assert len(attempts_payload) == 3
    assert all(item["outcome"] == "http_error" for item in attempts_payload)
    assert len(webhook_target.requests) == 3


def test_deduplicates_same_url_and_payload_within_ten_seconds(
    app_client: TestClient,
    webhook_target,
) -> None:
    first = app_client.post(
        "/webhooks",
        json={"target_url": webhook_target.url, "payload": {"event": "dedup", "order_id": 4}},
    )
    second = app_client.post(
        "/webhooks",
        json={"target_url": webhook_target.url, "payload": {"order_id": 4, "event": "dedup"}},
    )
    listing = app_client.get("/webhooks")

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert first.json()["id"] == second.json()["id"]
    assert len(listing.json()) == 1


def test_requeues_processing_webhook_on_startup(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "restart_safety.db"
    import os
    import sys

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "100"
    os.environ["RETRY_DELAY_SECONDS"] = "0"
    os.environ["DELIVERY_TIMEOUT_SECONDS"] = "1"
    os.environ["WORKER_MAX_CONCURRENCY"] = "1"

    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)

    db_module = import_module("app.db")
    models_module = import_module("app.models")
    worker_module = import_module("app.services.delivery_worker")
    db_module.init_db()

    session = db_module.SessionLocal()
    webhook = models_module.WebhookRequest(
        target_url="http://127.0.0.1:9/unreachable",
        payload_json='{"restart":true}',
        payload_hash="restart-hash",
        status=models_module.WebhookStatus.PROCESSING,
    )
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    webhook_id = webhook.id
    session.close()

    async def idle_run_loop(self) -> None:
        await self._stop_event.wait()

    monkeypatch.setattr(worker_module.DeliveryWorker, "_run_loop", idle_run_loop)

    app_module = import_module("app.main")
    with TestClient(app_module.app) as client:
        response = client.get(f"/webhooks/{webhook_id}")
        response.raise_for_status()
        payload = response.json()

    assert payload["status"] == "pending"

    db_module.engine.dispose()
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    for variable in (
        "DATABASE_URL",
        "WORKER_POLL_INTERVAL_SECONDS",
        "RETRY_DELAY_SECONDS",
        "DELIVERY_TIMEOUT_SECONDS",
        "WORKER_MAX_CONCURRENCY",
    ):
        os.environ.pop(variable, None)


def test_handles_burst_of_one_hundred_requests(app_client: TestClient, webhook_target) -> None:
    payloads = [{"event": "burst", "order_id": index} for index in range(100)]

    def submit(payload: dict) -> dict:
        response = app_client.post("/webhooks", json={"target_url": webhook_target.url, "payload": payload})
        assert response.status_code == 202
        return response.json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(submit, payloads))

    webhook_ids = [item["id"] for item in responses]
    details = wait_for_all_delivered(app_client, webhook_ids=webhook_ids)
    listing = app_client.get("/webhooks")
    listing.raise_for_status()

    assert len(responses) == 100
    assert len(set(webhook_ids)) == 100
    assert all(item["deduplicated"] is False for item in responses)
    assert all(item["status"] == "pending" for item in responses)
    assert len(details) == 100
    assert all(item["status"] == "delivered" for item in details)
    assert all(item["attempt_count"] == 1 for item in details)
    assert len(webhook_target.requests) == 100
    assert len(listing.json()) == 100
