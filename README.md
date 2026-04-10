# Webhook Delivery System

Proof-of-concept microservice for reliable webhook delivery to external systems.

## Features

- `POST /webhooks` accepts a webhook request (`target_url` + `payload`) and stores it in the database.
- `GET /webhooks` returns the list of stored operations.
- `GET /webhooks/{webhook_id}` returns details and current status of a selected webhook.
- `GET /webhooks/{webhook_id}/attempts` returns the delivery attempt history.
- A background worker pulls `pending` records, performs `POST` requests, stores attempt history, and handles retries.
- The system ignores duplicates with the same `target_url` and the same payload within a 10-second window.

## Architecture

The system has two logical parts:

- FastAPI API accepts requests, validates input, performs deduplication, and stores records in SQLite.
- A background worker started with the application picks up pending webhooks and delivers them to external systems.

Request intake is fully separated from processing in the logical sense:

- the client gets a `202 Accepted` response immediately after the record is stored,
- the request does not wait for the external endpoint result,
- actual delivery is handled separately by the worker.

In this PoC, both parts still run inside the same application process.

## Storage

SQLite is used as the data store.

Benefits for this task:

- very simple setup,
- no separate database server required,
- sufficient for a PoC and local development.

Limitations:

- SQLite is suitable for a PoC, but under higher concurrency it is weaker than a database such as Postgres,
- it is not the best target for heavy production workloads,
- in production, a natural next step would be Postgres and a separate worker process.

## Data model

### `webhook_requests`

Main table representing webhook delivery requests.

It stores, among others:

- `target_url`
- `payload_json`
- `payload_hash`
- `status`
- `attempt_count`
- `max_attempts`
- `next_attempt_at`
- `last_error`
- `created_at`
- `updated_at`
- `delivered_at`

### `webhook_attempts`

History of individual delivery attempts:

- attempt number,
- start and finish timestamps,
- attempt result,
- HTTP response code,
- response excerpt,
- error message.

## Why payload hashing exists

`payload_hash` is used for efficient deduplication.

The payload is first normalized into canonical JSON, then hashed with SHA-256. This lets the service compare payloads reliably even if JSON key order differs, while still keeping the full `payload_json` for actual delivery.

## Retry and reliability

- Success means an HTTP response in the `2xx` range.
- HTTP errors and network errors are stored as failed attempts.
- The system performs at most 3 delivery attempts.
- Retries do not block other jobs because failed webhooks return to `pending` with a scheduled `next_attempt_at`.
- After restart, records left in `processing` are reset back to `pending`.
- The number of concurrent deliveries is limited by `WORKER_MAX_CONCURRENCY`.

## Local run

Requirements:

- Python 3.12+
- installed project dependencies

Example using the local `.venv`:

```powershell
.venv\Scripts\uvicorn.exe app.main:app --reload
```

The application will be available at:

- `http://127.0.0.1:8000`
- Swagger docs: `http://127.0.0.1:8000/docs`

## Docker Compose run

Run:

```powershell
docker compose up --build
```

Services:

- API: `http://127.0.0.1:8000`
- Swagger docs: `http://127.0.0.1:8000/docs`
- Local test endpoint (`go-httpbin`): `http://127.0.0.1:8081`

The SQLite database is stored in a Docker volume mounted at `/data/webhooks.db`, so data survives container restarts.

## Configuration

Supported environment variables:

- `APP_NAME`
- `DATABASE_URL`
- `WORKER_POLL_INTERVAL_SECONDS`
- `WORKER_MAX_CONCURRENCY`
- `DELIVERY_TIMEOUT_SECONDS`
- `RETRY_DELAY_SECONDS`

Example:

```powershell
$env:DATABASE_URL = "sqlite:///custom.db"
$env:WORKER_MAX_CONCURRENCY = "5"
.venv\Scripts\uvicorn.exe app.main:app --reload
```

## Example API usage

### Create a webhook

```http
POST /webhooks
Content-Type: application/json

{
  "target_url": "https://example.com/webhook",
  "payload": {
    "order_id": 123,
    "status": "created"
  }
}
```

Example response:

```json
{
  "id": 1,
  "target_url": "https://example.com/webhook",
  "status": "pending",
  "deduplicated": false,
  "created_at": "2026-04-10T10:00:00Z"
}
```

To test delivery against the local `go-httpbin` container, you can submit:

```json
{
  "target_url": "http://httpbin:8080/post",
  "payload": {
    "order_id": 123,
    "status": "created"
  }
}
```

## Tests

End-to-end tests are implemented with `pytest`.

Run:

```powershell
.venv\Scripts\pytest.exe -q
```

Covered scenarios:

- happy path,
- retry with eventual success,
- final failure after 3 attempts,
- deduplication in a 10-second window,
- restart recovery,
- burst of 100 requests in a short time.

## Status Against Task Requirements

Implemented requirements:

- REST API for creating and reading webhook operations,
- asynchronous background processing,
- restart resilience,
- retry up to 3 attempts,
- operation history,
- concurrency limit,
- `URL + payload` deduplication within 10 seconds,
- burst traffic handling verified with an end-to-end test.
