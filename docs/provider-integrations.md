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

## TMDB

The TMDB provider uses API v3 search and detail endpoints with application-level Bearer authentication. Create an API
Read Access Token in a TMDB account and supply it only at runtime:

```bash
export MEDIA_RECOMMENDER_TMDB_API_TOKEN="replace-with-tmdb-api-read-access-token"
```

The default API URL is `https://api.themoviedb.org/3/`, metadata language is `en-US`, poster size is `w500`, and
backdrop size is `w1280`. Override them when needed with `MEDIA_RECOMMENDER_TMDB_BASE_URL`,
`MEDIA_RECOMMENDER_TMDB_LANGUAGE`, `MEDIA_RECOMMENDER_TMDB_POSTER_SIZE`, and
`MEDIA_RECOMMENDER_TMDB_BACKDROP_SIZE`. The language must use a value such as `cs-CZ`; image sizes accept TMDB width
values such as `w342` or `original`.

`TmdbMetadataProvider` sends the token only in the `Authorization` header. It maps TMDB IDs plus available IMDb, TVDB,
and Wikidata IDs into provider-independent namespaces. Poster and backdrop paths become absolute URLs using the
configured image base URL and sizes. Missing dates, runtime, countries, genres, external IDs, or artwork remain absent
instead of being inferred.

## Jellyfin

Jellyfin is a personal-library source rather than the catalog metadata provider. `JellyfinClient` validates the
configured user through the supported user endpoint and enumerates only that user's accessible movies and series.
`JellyfinLibrarySynchronizer` normalizes the response and delegates identity resolution and persistence to the shared
`LibrarySynchronizationService`.

Configure the server, API key, and selected Jellyfin user only at runtime:

```bash
export MEDIA_RECOMMENDER_JELLYFIN_BASE_URL="https://jellyfin.example.invalid/"
export MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN="replace-with-jellyfin-api-key"
export MEDIA_RECOMMENDER_JELLYFIN_USER_ID="replace-with-jellyfin-user-id"
```

The API key is sent only in the `X-Emby-Token` header. It is masked by validated settings and never included in normal
provider errors. The selected Jellyfin identity is stored as a mapping to the internal profile; it never becomes the
application's canonical user identity.

Each complete successful response updates library presence, explicit watched/unwatched state, play count, last-played
time, and synchronization provenance. Missing `UserData` means unknown watch state rather than unwatched. Items no
longer returned by a successful complete synchronization remain recorded but become unavailable. Retrieval failures
happen before the application service runs, so they do not remove or rewrite previously valid local state.

Jellyfin item IDs are retained alongside recognized IMDb, TMDB, and TVDB IDs. The shared identity resolver tries these
exact IDs first and then uses its deterministic metadata fallback. Unresolved, ambiguous, and individually malformed
items are reported in the synchronization result and are not silently attached to catalog entries.
