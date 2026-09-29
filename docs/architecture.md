# Architecture notes

The agent owns cross-service orchestration. HR only reads employee data, IAM owns access requests and approval/audit state, and ITSM owns tickets. If a ticket call fails after a successful IAM request, the workflow reports partial completion with the IAM request ID retained for repair.

The gateway transparently proxies Streamable HTTP. It verifies a JWT, applies route scopes, and forwards the request without interpreting MCP JSON-RPC. IAM passes through a request-level round-robin balancer. Both IAM replicas share PostgreSQL and Redis, so normal stateless MCP 2026-07-28 requests need no sticky session.

`X-Request-ID` is accepted when safe or generated, forwarded, and included in structured service logs. Metrics are JSON for demonstration. The mock IAM data is persisted in PostgreSQL with a Docker volume. ITSM tickets are in memory and disappear on API restart. Redis rate-limit keys expire; tools fail closed during Redis outages. HR/ITSM clients use a small circuit breaker and bounded GET retries.

For production, replace the mock APIs and local JWT issuer, configure per-service audiences, TLS/WAF, external secrets, durable ticket storage, highly available Redis/PostgreSQL, an orchestrator for replicas, and Prometheus/OpenTelemetry collectors.
