# CampaignBridge

A runnable voicebot campaign integration portfolio project by **Aisosa Noze-Otote**. Built with AI assistance. All accounts and outcomes are synthetic; no employer code, customer data or proprietary systems are included.

## What it demonstrates

Customer account input → validation → SQLite campaign records → HTTP call-outcome webhook → atomic record update and outbox entry → simulated follow-up delivery.

The integration boundary uses traditional, deterministic automation: account balances, record matching, duplicate detection and delivery decisions must be predictable. An upstream conversational AI could classify a call, but this project receives a structured outcome; it does not claim to run a voice agent or LLM.

## Run

Python 3.10 or newer; no third-party dependencies.

```sh
python3 server.py
```

Open **http://127.0.0.1:8510**. Keep the terminal running while using the app. Data survives restarts in `demo.sqlite3`, which is excluded from Git. For a fresh independent walkthrough, run `python3 server.py --db fresh-demo.sqlite3 --port 8511` and open port 8511.

```sh
python3 -m unittest discover -s tests -v
```

## Walkthrough (about two minutes)

1. Load three sample accounts. Balances are integer minor units, not floats.
2. Send the promise-to-pay outcome. The account changes and one pending follow-up appears.
3. Replay the same event. The response marks it as a duplicate; there is still only one job.
4. Simulate destination failure. The job becomes `retry`, with its error and attempt count retained.
5. Wait two seconds, then deliver due follow-ups. One simulated receipt appears.
6. Send an invalid account event. The API returns 400; no event or follow-up is created.

Use the live app for the walkthrough; the portfolio's animated preview is illustrative and runs in the browser only.

## API

POST requests require JSON and `Authorization: Bearer local-demo-token`. Override via `CAMPAIGN_TOKEN` and enter the same value in the UI. The default token is a public demo value, not a secret.

| Endpoint | Input / result |
|---|---|
| `POST /api/import` | Array of `{id, name, balance_cents}`; validates entire batch before writing. Reimport updates account fields and preserves outcome. |
| `POST /api/webhook` | `{event_id, customer_id, outcome}`; supported outcomes: `promise_to_pay`, `needs_help`, `declined`. |
| `POST /api/dispatch` | `{fail: true}` simulates HTTP 503; `{}` delivers due jobs to the local mock. |
| `GET /api/state` | Local inspection of customers, events, outbox and mock receipts. |

Replaying an identical event is safe. Reusing its ID with a changed payload returns **409**. A missing account, invalid balance or unsupported outcome returns **400**. `declined` does not create a follow-up. Outcome events never mark a balance paid: a promise is not verified payment.

## Reliability design

- Account update, event insertion and outbox insertion share one SQLite transaction.
- A write lock protects duplicate detection. Event IDs and payload hashes distinguish retries from conflicting data.
- Failed simulated deliveries retry after 2 and 4 seconds; a third failure goes to `dead_letter` for operator review.
- The mock destination deduplicates receipts by event ID. A real external destination must also support an idempotency key to avoid duplicate effects after ambiguous network failures.
- Tests cover rollback, duplicate events, conflicting payloads, declined outcomes, retries, dead letters, persistence and real local HTTP requests.

## Scope and limitations

This is a local, single-process demonstration. The destination is a database-backed simulation, **not a live CRM, SMS service or bank integration**. It has no webhook signature verification, production identity/authorization, retention policy, background worker, monitoring or TLS. State inspection is unauthenticated on loopback. Do not expose the server to a network or load personal data.

Events are processed in arrival order; a production campaign needs call IDs, ordering/version rules and consent/suppression checks. The demo queues follow-ups per unique event; distinct event IDs for the same call are not deduplicated. Retry dispatch is manual for demonstration. Do not infer production readiness or commercial performance from this project.

## Interview explanation

“I used a structured webhook to connect campaign outcomes to downstream work. Validation prevents missing account data entering the workflow. A transaction keeps account updates and the outbox consistent. Idempotency handles repeated events, while backoff and a dead-letter state make failure visible. The destination is mocked so the demo is safe and repeatable.”
