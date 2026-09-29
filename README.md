# Saviynt IGA / IAM MCP Server Demo

An **Identity Governance and Administration (IGA)** learning project inspired by **Saviynt Enterprise Identity Cloud** workflows. It uses the **Model Context Protocol (MCP)** to connect an AI agent to IAM access requests, HR identity lookup, and ITSM approval tickets. This is a mock integration: it does **not** call Saviynt APIs or provision real access.

Three independent MCP servers show how an agent combines HR, IAM, and ITSM tools. The gateway applies JWT route policy and forwards standard MCP Streamable HTTP traffic; an IAM load balancer spreads stateless requests over two replicas.

**Topics:** Saviynt IGA, Identity Governance and Administration, IAM, Identity and Access Management, Model Context Protocol, MCP server, AI agent, access request, least privilege, RBAC scopes, human approval, audit trail, idempotency, API gateway, Redis rate limiting, PostgreSQL, Streamable HTTP.

## Features

- **IAM MCP:** search users/applications, submit idempotent access requests, review and decide high-risk requests, inspect the audit trail, and read IAM resources/prompts.
- **HR MCP:** read employee, department, and manager data through a mock HR REST API.
- **ITSM MCP:** create idempotent approval tickets and retrieve tickets through a mock service desk API.
- **Agent orchestration:** discover namespaced tools across all three servers and complete an HR → IAM → ITSM workflow. The application requires human **YES** before an IAM approval or rejection tool executes.
- **Gateway and resilience concepts:** JWT and scope checks, request IDs, two IAM replicas, Redis-backed shared rate limits, dependency readiness, bounded timeouts, and a downstream circuit breaker.

```text
User / optional OpenAI agent
            |
      MCP gateway :8080
       /      |       \
 IAM LB :8081 HR :8002 ITSM :8003
    /     \       |       |
 :8011   :8012  HR API  ITSM API
    \      /       :9100    :9201
  IAM API :9000
       |
  PostgreSQL :5432        Redis :6380
  (access and audit)       (shared rate limits)
```

IAM is also available directly at `:8001/mcp`. Port 8000 was occupied locally, so IAM uses 8001; ports 9200 and 6379 were occupied, so the ITSM API uses 9201 and Redis maps host 6380 to container 6379. URLs are configurable. The installed SDK negotiates MCP protocol `2026-07-28`.

## Quick start (Windows PowerShell)

Requires Python 3.11+, Docker Desktop with Compose, and PowerShell. From a new checkout:

```powershell
git clone https://github.com/Rajendra1996/IAM-MCP-Server.git
cd IAM-MCP-Server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
docker compose up -d --wait
```

The last command starts PostgreSQL and Redis. In **separate terminals**, run these commands from the repository root with `.venv` active. Set the same `IAM_JWT_SECRET` in each MCP, gateway, client, and agent terminal; for this local demo you can use the example below:

```powershell
$env:IAM_JWT_SECRET='dev-example-replace-with-your-own-secret-at-least-32-bytes'
# One command per terminal:
uvicorn mock_services.iam_api:app --host 127.0.0.1 --port 9000
uvicorn mock_services.hr_api:app --host 127.0.0.1 --port 9100
uvicorn mock_services.itsm_api:app --host 127.0.0.1 --port 9201
python -m app.iam.mcp_server
python -m app.hr.mcp_server
python -m app.itsm.mcp_server
$env:IAM_MCP_PORT='8011'; $env:INSTANCE_ID='iam-a'; python -m app.iam.mcp_server
$env:IAM_MCP_PORT='8012'; $env:INSTANCE_ID='iam-b'; python -m app.iam.mcp_server
python -m app.gateway.load_balancer
python -m app.gateway.app
```

The IAM replicas and load balancer are needed for gateway IAM routing. In a separate client terminal, set the same JWT secret, generate an employee token, and run the multi-service demo:

```powershell
$env:IAM_JWT_SECRET='dev-example-replace-with-your-own-secret-at-least-32-bytes'
python -m scripts.generate_multi_token
$env:IAM_ACCESS_TOKEN='<employee-token>'
python -m clients.multi_mcp_client
```

The client connects independently to the three MCP servers and runs HR lookup → IAM SAP request → ITSM approval ticket. Gateway URLs are `http://127.0.0.1:8080/iam/mcp`, `/hr/mcp`, and `/itsm/mcp`. With `OPENAI_API_KEY` and `IAM_ACCESS_TOKEN` set, `python -m app.agent.orchestrator` starts the optional interactive agent. It discovers namespaced tools such as `hr_search_employee`, `iam_request_access`, and `itsm_create_ticket`, and requires terminal **YES** before IAM approval or rejection. Automated tests mock OpenAI responses.

For the optional full-container layout, stop conflicting local Python services and run `docker compose --profile full up -d --build --wait`. Containers use Docker service names (`postgres`, `redis`, `iam-api`, etc.). Plain `docker compose up -d` starts only PostgreSQL and Redis. Avoid `docker compose down -v` if you want to retain access requests. The full-container profile is included as a deployment example; the local-process path above is the exercised demo.

## Repository structure

| Path | Purpose |
| --- | --- |
| `app/iam`, `app/hr`, `app/itsm` | MCP servers, tools, service logic, REST clients |
| `app/gateway` | JWT route policy, HTTP proxy, IAM load balancer |
| `app/agent` | OpenAI orchestration, tool registry, human approval |
| `app/database` | PostgreSQL repository and access models |
| `app/shared`, `app/core` | Auth, Redis, rate limits, resilience, correlation, settings |
| `mock_services` | Fake IAM, HR, ITSM REST systems |
| `clients`, `scripts` | Demo clients and token helpers |
| `tests` | Unit, integration, and end-to-end checks |
| `deploy/docker`, `docs` | Container files and architecture notes |

## Boundaries

- The gateway allows IAM, HR, and ITSM routes, checks signed JWTs and route scopes, caps bodies at 256 KiB, and forwards Authorization, MCP headers, SSE, and `X-Request-ID`.
- Every MCP service verifies JWTs again. IAM approvals require `iam:approve`; HR reads require `hr:read`; ITSM writes require `itsm:create`.
- The application, outside the model, asks a human for **YES** before approval or rejection. Tool output is data, never authority to bypass the gate.
- PostgreSQL stores IAM requests and audit entries in transactions. Reusing an idempotency key returns the same request or ticket without a duplicate side effect.
- Redis enforces one fixed-window limit across IAM replicas with expiring keys. If Redis is unavailable, tools fail closed and readiness degrades.
- `/live` means a process is running; `/ready` checks dependencies. The IAM balancer can continue with one ready replica. `/metrics` exposes learning-oriented JSON counters and pool/Redis status.
- REST and database calls have bounded timeouts. HR/ITSM GETs retry transient failures; writes are not automatically retried. HR/ITSM REST clients have a CLOSED → OPEN → HALF_OPEN circuit breaker.

Normal MCP 2026-07-28 requests can move between IAM replicas because durable requests are in PostgreSQL and rate-limit state is in Redis. MCP itself does not provide load balancing, TLS, process supervision, WAF, or autoscaling.

```powershell
python -m pytest -q
python -m pytest --integration -q   # requires the local services above
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/ready
Invoke-RestMethod http://127.0.0.1:8081/metrics
```

This is a learning mock: local HS256 JWTs, fake enterprise APIs, in-memory ITSM tickets, one local gateway, JSON metrics, and Docker Compose. **Saviynt IGA is a conceptual reference, not a live connector.** Production replacements include a real identity provider with per-service audiences, Saviynt/HRIS/ServiceNow APIs, durable ticket storage, TLS/WAF/API gateway, managed PostgreSQL and Redis, secret management, an orchestrator, and Prometheus/OpenTelemetry export. See [architecture notes](docs/architecture.md).
