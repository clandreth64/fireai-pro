# FireAI Pro — Production Requirements (not met yet)

Milestone 1.5 is **not deployable**. Railway has not been modified. This page records what must be
true before any public or customer-facing deployment. None of it is in scope for Milestone 1.5.

| Area | Current state | Required before production |
|---|---|---|
| **Authentication** | Optional shared bearer token (`FIREAI_API_TOKEN`); otherwise unauthenticated. `/health` says so. | Real per-user identity (OIDC/SSO), per-organization tenancy, `owner` enforced on every job/deliverable query, audit log of access. Never expose publicly without it. |
| **Authorization** | None beyond the token | Role-based access (viewer / reviewer / engineer / admin); human-review approvals attributable to a person |
| **Job execution** | FastAPI `BackgroundTasks` inside the web process; max 2 concurrent; a restart strands jobs in `running` | Separate worker service + durable queue (Postgres- or Redis-backed), retries with idempotent stages, timeouts, cancellation, stuck-job detection |
| **Database** | SQLite file in `FIREAI_DATA_DIR` | Managed Postgres with migrations, backups, point-in-time recovery |
| **File storage** | Local container disk (`FIREAI_DATA_DIR/jobs/<id>`) — lost on redeploy unless a volume is mounted | Private object storage (S3/R2) with per-tenant prefixes, server-side encryption, short-lived signed URLs, retention/lifecycle rules, deletion on request |
| **Large-file performance** | Measured: 5 MB DWG → up to ~4.6 GB peak RSS during conversion audit; 10 MB DXF with dynamic blocks → 106 MB model JSON, ~2.6 GB RSS; worst overlay 135 s | Streaming/lighter DWG census; store source layer separately from the model; optional SVG; per-job memory limits in isolated workers |
| **Fonts** | Installed in `docker/Dockerfile` (fonts-dejavu-core) | Must be present in any runtime image; Railway's default Python builder does not provide them — drawing text would silently vanish from renders |
| **DWG conversion** | GNU LibreDWG 0.14.8597 built from a pinned, checksum-verified tarball; invoked as a separate process | Legal review of GPL obligations for distributing the binary in a product image (source offer, license text); decide long-term converter (LibreDWG vs. ODA membership vs. cloud API); conversion sandboxing (seccomp/cgroup limits), fuzz-hardening |
| **Upload safety** | Size limit (streaming), content sniffing, sanitized names, isolated job dirs | Malware scanning, per-tenant quotas, rate limiting |
| **Secrets** | None required by v2 code | Managed secret store; rotate anything previously used by v1 on Railway |
| **Observability** | Stage timings in reports | Structured logs, metrics, tracing, alerting on failure rates and review-trigger rates |
| **Engineering claims** | None made; UI and reports say "not for design or permit" | Keep until later milestones are independently validated by a licensed professional |
| **Data policy** | No data leaves the server; no AI used | Customer agreement on drawing handling, retention and any future AI use |
