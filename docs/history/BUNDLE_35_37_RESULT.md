BUNDLE 35–37 RESULT

Files created:
- `iam_api.py`
- `iam_api_client.py`
- `docker-compose.yml`
- `pytest.ini`
- `tests/conftest.py`
- `tests/test_iam_api.py`
- `tests/test_iam_api_client.py`
- `tests/test_database.py`
- `tests/test_server.py`
- `tests/test_agent.py`
- `tests/test_mcp_integration.py`
- `.gitignore`
- `.env.example`
- `README.md`
- `bundle35-37-evidence.json`
- `test-results.xml` (generated, ignored by Git)
- `BUNDLE_35_37_RESULT.md`

Files modified:
- `database.py` — PostgreSQL pool, repeatable schema, atomic creation/decision and audit transactions.
- `server.py` — REST lookups, required idempotency keys, safe errors, dependency health and pool metrics.
- `models.py` — idempotency keys and timezone-aware datetimes; explicit paginated error results.
- `requirements.txt` — frozen dependencies retained and extended; Windows marker on `pywin32` for Docker compatibility.
- `remote_client.py` — port 8001 and reusable authenticated remote connection.
- `client.py` — authenticated remote connection and access-request idempotency key.
- `agent.py` — authenticated remote connection, lazy OpenAI client initialization, and idempotency compatibility; human gate retained.
- `generate_token.py` — main guard prevents printing tokens when imported; CLI still generates both token types.
- `Dockerfile` — port 8001 and container host binding.
- `.dockerignore` — excludes historical databases and generated test XML.

PostgreSQL:
- container status: `iam-postgres`, running, `postgres:17`
- health: healthy
- storage: named Docker volume; request 57 survived `docker compose down` and `docker compose up -d --wait --wait-timeout 40`
- pool: shared process-level pool, min 2 / max 10, acquisition timeout 10 seconds

IAM API:
- status: running
- URL: `http://127.0.0.1:9000`

MCP:
- status: running
- URL: `http://127.0.0.1:8001/mcp`
- protocol: `2026-07-28`, verified through authenticated SDK discovery
- transport: Streamable HTTP with SSE responses for progress notifications
- port: 8001 selected by the user because an existing `llm-gateway` container occupies 8000

Tests:
- command: `.\.venv\Scripts\python.exe -m pytest --integration -q --junitxml=test-results.xml`
- total: 71
- passed: 71
- failed: 0
- errors: 0
- skipped: 0
- warnings: 10, from the existing short development JWT fallback
- dependency validation: `python -m pip check` passed
- Python compilation: passed
- Docker image: `docker build -t iam-mcp-bundle35-37 .` passed
- remote CLI: `remote_client.py` passed using a generated employee JWT
- original demo CLI: `client.py` passed, including progress callbacks
- token CLI: both generated JWTs verified; token values were withheld from validation logs
- agent: schema conversion tested against live MCP; human gate and conversation/tool loop tested with mocked model calls

Idempotency:
- first request id: 57
- duplicate request id: 57
- duplicate rows created: no
- duplicate creation audit events: no
- concurrent duplicate inserts: eight attempts produced one winner
- conflicting key reuse: rejected
- replay after approval: returned the original approved request without adding audit events
- audit failures: request creation and decision updates rolled back atomically

Authentication:
- employee test: PASS, `iam:basic`
- approver test: PASS, `iam:basic` and `iam:approve`
- missing, malformed, expired, incorrectly signed, wrong-audience and wrong-issuer JWTs: rejected

Authorization:
- employee approval blocked: yes; no decision write occurred
- employee rejection blocked: yes
- approver approval allowed: yes; `REQUEST_APPROVED` persisted
- approver rejection allowed: yes; `REQUEST_REJECTED` persisted
- allowlists and per-subject rate limiting: passed
- concurrent decisions: one winner and one decision audit event
- OpenAI agent human approval gate: preserved and tested

Health:
- database: ok
- iam_api: ok
- IAM API outage: HTTP 503 and safe lookup/request errors; API restarted afterward
- PostgreSQL outage: HTTP 503 and explicit storage errors; no misleading success
- recovery: HTTP 200 and persisted request readable through MCP without restarting the MCP server

Preserved data and scope:
- `iam.db` remains byte-for-byte unchanged; SHA256 `7646F79754B129282F69826FE5155838EFFBBB3103E0759163402FF799C89DB5`
- Runtime code no longer imports SQLite. Only `iam_api.py` reads the demo `USERS` / `APPLICATIONS` dictionaries.
- Resources, review prompt, pagination, audit retrieval, progress analysis, JWT scopes, allowlists, rate limiting, and the human approval gate remain available.
- Smoke rows 57 (approved) and 59 (needs approval) remain for inspection; automated tests removed only their own rows.
- Original source backups: `C:\Users\baral\AppData\Local\Temp\iam-bundle-35-37-20260929-215701`
- Bundle 38–40 was not implemented.

Resolved validation failures:
- Initial `python -m pytest --integration -q --junitxml=test-results.xml`: 64 passed / 2 failed with `TypeError: 'NoneType' object is not subscriptable` because bare `dict` return annotations produced text-only SDK results. Changed tool annotations to `dict[str, Any]`.
- Follow-up `python -m pytest --integration tests/test_mcp_integration.py -q`: 6 passed / 1 failed because progress notifications were dropped in JSON-only response mode. Enabled SSE responses within Streamable HTTP; the failed tests and full regression suite now pass.
- Initial manual outage script: `KeyError: 'success'` after the API was restarted before tool checks finished. Repeated the entire check while the API remained stopped; health and all three tools returned the expected degraded/safe results.
- Initial protocol diagnostic used legacy `initialize` and observed its compatible `2025-11-25` handshake. The installed SDK uses modern discovery for `2026-07-28`; authenticated discovery and the explicit integration assertion confirm that protocol.

Known issues:
- The existing default development JWT secret is 25 bytes and triggers PyJWT key-length warnings. JWT tests passed. The PowerShell setup in `README.md` supplies a longer example and explains matching server/client secrets.
- A paid OpenAI model request was not made. Agent transport, schema translation, human approval, idempotency compatibility, and conversation-loop behavior were validated.

FINAL STATUS:
PASS
