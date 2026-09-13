# Provider integration conventions

Metadata providers implement the provider-independent `MetadataProvider` application contract. Search results and
details crossing into application services use application and domain types; provider response models never cross that
boundary.

Keep each concrete provider separated into three concerns:

```text
integrations/<provider>/
├── client.py   # endpoint calls and provider error semantics
├── models.py   # Pydantic v2 response DTOs
└── mapper.py   # conversion from DTOs to domain models
```

Use `ProviderSettings` as the validated configuration base. A concrete provider should set its own environment prefix,
keep credentials in `SecretStr` fields, and must never put secrets in URLs, logs, or application exception messages.
Pass credentials in the provider's supported authorization header where possible.

`ProviderHttpClient` owns one `httpx.AsyncClient` connection pool. Create it once for a provider lifecycle and close it
explicitly, preferably with `async with`. Its timeouts are configured independently for connection, reads, writes, and
pool acquisition.

The shared transport performs no automatic retries. It translates timeouts, transport failures, authentication
failures, rate limits, upstream failures, other non-success responses, malformed JSON, and DTO validation failures into
explicit provider exceptions. A concrete provider may add a bounded retry only when all of the following are true:

* the request is idempotent;
* the failure is documented as transient by the provider;
* rate-limit responses honor the provider's reset or `Retry-After` guidance;
* the policy has a small attempt limit and backoff;
* offline tests verify the exact retry behavior.

Tests use `httpx.MockTransport` or a provider fake and must never require network access or real credentials.
