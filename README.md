# Media Recommender

Media Recommender is a self-hosted application for discovering what to watch by combining personal viewing history,
ratings, preferences, media metadata, streaming availability, and local media libraries.

The project currently provides a standalone Web UI and REST API, with MCP integration and optional AI assistance
planned as future capabilities built on the same underlying core.

Media Recommender is intentionally a small self-hosted application for one household. It is not a multi-tenant SaaS
product or a generic media-management platform; future design decisions should favor the smallest maintainable
solution for demonstrated household use cases.

## Goals

Media Recommender should help answer questions such as:

> Find me a science-fiction movie available on Netflix CZ or in my Jellyfin library that I have not watched yet. Exclude
  Asian, African, and South American productions, horror, and comedy, and prefer movies shorter than 150 minutes.

Recommendations should be based on structured data whenever possible rather than relying on an AI model to remember or
guess facts about media.

The application should remain useful without any AI provider configured.

## Core concepts

Media Recommender combines several kinds of information:

* media metadata such as title, year, genres, runtime, production countries, and external identifiers;
* streaming availability for a selected region, including access type, freshness, and source provenance;
* local media availability, initially including Jellyfin;
* personal viewing history;
* personal ratings and preferences;
* deterministic filtering and recommendation rules;
* optional AI-assisted natural-language interaction (planned);
* Personal viewing history, ratings, preferences, and provider mappings are user/profile-specific,
  while media catalog metadata is shared application data.

## Architecture

The target architecture keeps every interface on the same application and recommendation core while integrations
provide external data through explicit boundaries.

```text
                    Media Recommender

             ┌────────────┼────────────┐
             │            │            │
          Web UI        REST API       MCP
             │            │            │
             └────────────┼────────────┘
                          │
                  Application Services
                          │
                  Recommendation Core
                          │
          ┌───────────────┼───────────────┐
          │               │               │
     Media metadata   Personal data   Availability
          │               │               │
         TMDB          Netflix         Streaming
                       Jellyfin          services
                          │
                          ▼
                    Local database
```

### Design principle

> Integrations provide data. The application core owns the domain model and recommendation logic. Web, REST, and MCP
  consume the same application services.

Provider-specific behavior should therefore stay outside the central recommendation logic whenever possible.

### Current implementation

Phase 1 implements the metadata and catalog slice of this architecture. Provider-independent domain models represent
movies and TV shows, `CatalogService` orchestrates the `MetadataProvider` and `MediaCatalog` boundaries, TMDB supplies
normalized metadata, and SQLite stores the shared catalog behind SQLAlchemy repositories and explicit Alembic
migrations. The application service preserves internal identities while synchronizing external metadata and is ready
for later interfaces to consume without exposing TMDB DTOs or ORM records.

The Phase 2 personal-data foundation adds an explicit internal profile owner, an implicit default profile, viewing
events, ratings and reactions, preferences and exclusions, and external provider-profile mappings. Personal records
reference the shared catalog rather than duplicating media metadata. Source record and synchronization identities make
later supported imports repeatable without treating a provider identity as the application user.

Phase 3 completes the Web workflows on top of these application services. The server-rendered UI includes the home page,
a read-only runtime settings overview (`/settings`), provider status inspection (`/providers/status`), local Netflix
viewing activity CSV import controls (`/imports/netflix`), Jellyfin library synchronization
(`/synchronizations/jellyfin`), regional streaming-availability refresh (`/availability/refresh`), structured
recommendation search (`/recommendations`), and media detail and personal rating management (`/media/{media_id}`).
Provider configuration is supplied through environment settings; the Web UI is intentionally read-only for settings.
MCP integration, AI assistance, and multi-user authentication remain planned future capabilities.

## Main project areas

### Application Core

Defines typed movie and TV-show domain models, configuration, persistence contracts, and catalog application services.

### Media Sources and Metadata

TMDB currently provides normalized movie and TV-show search and detail metadata, including genres, production
countries, runtime, artwork, release information, external identifiers, and regional watch-provider availability.

### Personal Media Data

The provider-independent domain and SQLite persistence layers can store profile-owned viewing history, ratings,
explicit like/dislike state, preferences, exclusions, and provider-profile mappings. Viewing history is event-based,
so repeated watches remain distinct. Watch status preserves `watched`, explicit `unwatched`, and `unknown` as separate
states. A rating does not implicitly mark an item as watched, and missing records continue to represent unknown state.

The initial single-user workflow uses one deterministic implicit default profile. Profile management, switching,
authentication, and authorization are not implemented.

Initial sources include:

* local Netflix viewing-history CSV and supported ratings/interactions files;
* Jellyfin movie and TV-show library contents, watched state, play count, and last-played metadata.

Additional providers may be added later.

### Recommendations and AI

Deterministic filtering supports media type, genres, production countries and broad regions, runtime, release ranges,
profile-specific watch state, ratings/reactions, persisted exclusions, Jellyfin presence, and region-aware streaming
availability. Results preserve structured exclusion reasons and do not require AI. Detailed semantics are documented
in [Deterministic recommendation filtering](docs/recommendation-filtering.md).

Accepted candidates can be ranked with documented integer weights using explicit profile preferences, ratings, and
like/dislike state. Results include structured factual matches, score contributions, availability, personal state,
and missing-data warnings. AI integration is not implemented yet.

AI support is optional and should primarily provide:

* natural-language interpretation;
* conversational refinement of recommendations;
* explanation of why a title was recommended.

Structured filtering and recommendation logic should not depend on an AI provider.

### Web Application

The HTTP/Web foundation uses FastAPI and server-rendered Jinja2 templates. Create the base application with
`media_recommender.web.create_app()`. The factory initializes interface concerns and a production lifespan; the
lifespan applies pending migrations, builds the database engine, repositories, application services, and configured
provider integrations, and attaches them to application state. Feature routes define their own FastAPI `Depends`
dependencies and resolve those services from application state; tests can use the application's native
`dependency_overrides` mapping.

Versioned business API routes are available under `/api/v1`. Operational health endpoints are versionless and have
distinct semantics: `GET /health/live` verifies only that the HTTP process can serve requests, `GET /health/ready`
reports database accessibility and migration state, and `GET /health/providers` reports safe provider configuration and
operational state without affecting liveness or readiness. `GET /api/v1/media/{media_id}` retrieves normalized shared
catalog metadata through the injected `CatalogService`; it returns movie or TV-show facts, but never profile-owned
state, streaming availability, library presence, provider transport data, or ORM records. The shared page layout and
static CSS form the responsive, accessible baseline for later server-rendered screens.

`POST /api/v1/recommendations` is a deterministic, non-AI, read-only computation API. It maps explicit JSON hard
constraints to the injected Phase 2 recommendation facade and returns only ordered accepted ranked recommendations
with factual structured explanations. It does not expose rejected candidates, filter decisions, or exclusions.

`GET /imports/netflix` renders an accessible multipart form for one Netflix Viewing Activity CSV and a stable profile
label. `POST /imports/netflix` enforces the configured size limit at the request boundary before multipart parsing,
then validates the CSRF token, filename, content type, and header before staging the upload in a short-lived private
temporary file and delegating to the Netflix-only application facade. The page renders aggregate counts only and never
persists or renders the upload, filename, or profile label. The default maximum upload size is 10 MiB and can be
changed with `MEDIA_RECOMMENDER_NETFLIX_UPLOAD_MAX_BYTES`.

`GET /synchronizations/jellyfin` renders an accessible form for one Jellyfin library synchronization. The control is
enabled only when Jellyfin configuration is valid, and `POST /synchronizations/jellyfin` applies the same session-bound
CSRF and exact-origin check before re-validating configuration and delegating to the Jellyfin-only application facade.
The page renders aggregate, privacy-safe counts only, distinguishes absent or invalid configuration, authentication
failure, transient provider failure, partial results, and success, and never renders provider URLs, tokens, external
user identities, item data, or raw errors. A Jellyfin failure does not affect `/settings` or `/providers/status`, and a
repeat submission is an explicit new snapshot rather than an automatic retry.

`GET /availability/refresh` renders an accessible form for one regional streaming-availability refresh. The control is
enabled only when TMDB configuration is valid, and `POST /availability/refresh` applies the same session-bound CSRF and
exact-origin check before re-validating configuration and delegating to the availability-only application facade. The
page renders aggregate, privacy-safe counts only, distinguishes absent or invalid configuration, authentication
failure, transient provider failure, partial results (including a safe aggregate failure classification when some
titles fail), and success, and never renders provider URLs, tokens, external identities, item data, or raw errors. A
failed refresh does not affect `/settings`, `/providers/status`, `/imports/netflix`, or `/synchronizations/jellyfin`,
and a repeat submission is an explicit new snapshot rather than an automatic retry.

The implemented server-rendered UI includes the shared application shell, home page, read-only settings page
(`/settings`), provider status inspection (`/providers/status`), the Netflix Viewing Activity import page
(`/imports/netflix`), the Jellyfin library synchronization page (`/synchronizations/jellyfin`), the regional
streaming-availability refresh page (`/availability/refresh`), the structured recommendation workflow
(`/recommendations`), and media detail with personal state controls (`/media/{media_id}`).

`GET /settings` displays safe runtime configuration facts in a read-only overview: whether TMDB and Jellyfin
settings are configured and the configured default region. It does not verify provider connectivity or expose
credentials or provider configuration values. Provider configuration is managed via environment variables rather
than UI editing.

`GET /recommendations` renders the criteria form and `POST /recommendations` applies session-bound CSRF and
exact-origin checks before delegating to the deterministic recommendation facade. Result cards preserve service
ordering, expose known availability, watched/rated state, and structured recommendation reasons, and link directly
to media detail screens.

`GET /media/{media_id}` renders detailed media facts, regional streaming availability, local Jellyfin library
presence, and profile-owned personal state (watched state and personal rating). `POST /media/{media_id}` allows
setting or updating the user's personal rating, protected by session-bound CSRF and exact-origin validation.

Web and API routes translate HTTP models to application-service contracts; they must not duplicate business logic or
render ORM records and provider transport DTOs directly. API responses use a common JSON error envelope where an
interface handler owns the error. Framework HTTP and validation responses retain FastAPI/Starlette semantics, and
future feature-specific handlers take precedence over the generic unexpected-error fallback.

State-changing operations must never use `GET`. Safe and idempotent method semantics must be stated by each endpoint.
Browser-originated state-changing requests use same-origin protection: server-rendered forms include a session-bound
CSRF token, and an exact `Origin` check rejects cross-origin submissions. The session cookie is `HttpOnly` and
`SameSite=Lax`; set `MEDIA_RECOMMENDER_WEB_SESSION_SECRET` in production so signed sessions survive restarts. In
contrast, `POST /api/v1/recommendations` performs read-only computation and is not state-changing.

### MCP Integration

Planned; not implemented yet. The repository does not currently expose MCP tools.

MCP is an interface to the application rather than the application core itself.

### Deployment and Operations

The supported baseline deployment is a single production Docker image plus a reference Docker Compose file. It requires
only Docker/Compose and configured external providers; it does not require Kubernetes, Redis, PostgreSQL, a reverse
proxy, or an external job queue.

Build the image from a clean checkout:

```bash
docker build -t media-recommender:local .
```

Run it with a persistent data volume and runtime configuration:

```bash
docker run --rm \
  -p 8000:8000 \
  -v media-recommender-data:/data \
  -e MEDIA_RECOMMENDER_WEB_SESSION_SECRET="replace-with-a-long-random-session-secret" \
  media-recommender:local
```

The reference [`compose.yaml`](compose.yaml) provides the same deployment with a named volume:

```bash
cp .env.example .env
# Edit .env and replace the placeholder session secret and optional provider values.
docker compose up --build
```

The application listens on port `8000` and exposes `GET /health/ready`, which the image healthcheck uses. The image runs
as a non-root user, contains no development dependencies, tests, documentation, local databases, or credentials, and
stores all mutable state under `/data` (`MEDIA_RECOMMENDER_DATABASE_PATH=/data/media-recommender.db`). Because `/data`
is a mounted volume, SQLite data survives container recreation and upgrades.

Runtime configuration and secrets are supplied through environment variables or a Compose `.env` file and are never
baked into the image. `MEDIA_RECOMMENDER_WEB_SESSION_SECRET` must be set so signed sessions survive restarts; provider
credentials are optional and documented in [Provider integration conventions](docs/provider-integrations.md).

The container stops gracefully on `SIGTERM`, and `docker compose stop` waits for the configured grace period before
terminating the process. Schema upgrades are explicit and migration-driven: startup applies all pending Alembic
migrations before serving, and a migration failure aborts startup instead of serving an incompatible schema. Before
applying pending migrations to an existing database, the application creates and verifies a consistent SQLite snapshot
under `/data/backups/`. The manual command remains available for offline inspection:

```bash
docker compose run --rm media-recommender alembic upgrade head
```

Startup ordering, health semantics, the upgrade path, SQLite backup and restore, logging, and provider-outage behavior
are documented in [Operations](docs/operations.md).

## Non-goals

The project is not intended to:

* scrape or automate the Netflix web interface;
* bypass streaming service restrictions;
* store credentials or personal viewing data in the source repository;
* require an AI service for basic recommendations;
* duplicate full media-management functionality already provided by applications such as Jellyfin.

## Privacy

Personal viewing history, ratings, API credentials, tokens, and local application databases are runtime data and
must never be committed to the repository.

The application should prefer local storage for personal data wherever practical.

## Project status

The application can represent movies and TV shows, normalize TMDB metadata, persist a shared catalog and profile-owned
personal media state in SQLite, and expose those workflows through provider-independent application contracts. The
automated tests use synthetic data, temporary databases, and mock transports, so normal validation is fully offline.

The repository provides a FastAPI application factory, a production ASGI entry point with a startup lifecycle, a
server-rendered application shell, documented liveness, readiness, provider-health, media-read, REST recommendation, and
Phase 3 server-rendered Web workflows (settings, provider status, Netflix import, Jellyfin sync, availability refresh,
recommendations, and media detail with personal state editing), and a production Docker image with a reference
Docker Compose deployment. There is no AI behavior or MCP interface yet.
Architecture and public interfaces may change before the first stable release.

Multi-user profile management and authentication are planned future capabilities and are not part
of the initial v0.1.0 scope.

## Local development

Install [uv](https://docs.astral.sh/uv/) and ensure Python 3.14 is available. The project also provides an optional
[Task](https://taskfile.dev/) command runner. With Task installed, bootstrap the development environment and run all
quality checks with:

```bash
task bootstrap
task pre-commit:all
```

For the regular incremental workflow, `task pre-commit` checks staged files only. `task pre-commit:add` first stages
all working-tree changes and then runs the same staged-file checks.

The equivalent direct commands create the reproducible environment from the committed lockfile, install the Git
hooks, and run the complete local validation suite:

```bash
uv sync --frozen
uv run pre-commit install
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pydoclint src
uv run pytest
uv run pre-commit run --all-files
```

Application code uses the `src/media_recommender` package layout. Runtime dependencies are kept separate from the
development tools declared in the `dev` dependency group.

The current stack uses Python 3.14, uv, Pydantic and pydantic-settings, HTTPX, SQLAlchemy 2.x with aiosqlite,
Alembic, FastAPI, Jinja2, pytest, Ruff, Pyright, pydoclint, and pre-commit. FastAPI and Jinja2 are confined to the
outer Web interface layer and do not change the application core's provider-independent boundaries.

### Continuous integration

GitHub Actions runs `uv sync --all-groups --frozen` followed by the same `pre-commit --all-files` quality gate used
locally for every pull request and push to `main`. The workflow uses Python 3.14 and the committed `uv.lock`; it does
not receive provider credentials and does not upload databases, environment files, provider payloads, or other runtime
artifacts.

### Runtime configuration

The SQLite catalog location and live TMDB access are configured only at runtime. These placeholders demonstrate the
minimum settings; the TMDB token is unnecessary for tests and CI because provider calls are mocked.

```bash
export MEDIA_RECOMMENDER_DATABASE_PATH="data/media-recommender.db"
export MEDIA_RECOMMENDER_LOG_LEVEL="INFO"
export MEDIA_RECOMMENDER_TMDB_API_TOKEN="replace-with-tmdb-api-read-access-token"
```

Optional TMDB and Jellyfin configuration, endpoint, and timeout settings are documented in
[Provider integration conventions](docs/provider-integrations.md).
The complete default-profile synchronization and recommendation flow is documented in
[Phase 2 application workflows](docs/phase-2-workflows.md).

### Local database

The shared media catalog uses SQLite. Its path defaults to `data/media-recommender.db` and can be changed with the
`MEDIA_RECOMMENDER_DATABASE_PATH` environment variable. Database files under `data/`, SQLite sidecar files, and files
ending in `.db` are ignored by Git because catalog data is private runtime state.

Schema management is explicit and migration-driven. Upgrade the configured database before running code that uses the
catalog repository:

```bash
task db:upgrade
# Equivalent command:
uv run alembic upgrade head
```

Create a migration after intentionally changing the persistence models, review the generated operations, and verify
that it upgrades a clean database:

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

Creating an application database engine or session does not create or recreate tables. The production lifespan applies
pending migrations at startup and aborts startup when a migration fails, so the application never serves an incompatible
schema. See [Operations](docs/operations.md) for the upgrade, backup, and recovery procedures.

### Catalog application service

`CatalogService` is the provider-independent entry point for catalog workflows currently used by the Web and REST
interfaces, including `GET /api/v1/media/{media_id}`; future MCP interfaces can reuse it. It searches explicitly
selected configured metadata providers, synchronizes normalized detail into the
catalog, and retrieves persisted media by internal or external identity. A refresh preserves the internal application
identity while replacing catalog metadata with the provider's latest normalized detail; missing detail fields clear
previously stored values instead of retaining stale metadata.

Each repository write owns one database transaction. Provider calls complete before that transaction starts, and a
persistence error rolls back the complete write. Provider failures and persistence failures are propagated for an
interface layer to translate, while missing providers, missing details, and mismatched provider results use explicit
application-service errors. External-ID conflicts are never resolved through title/year heuristics.

### Media identity resolution

`MediaIdentityResolver` attaches normalized provider facts to shared catalog items without provider-specific DTOs or
an AI service. It prefers exact and authoritative cross-provider identifiers, then considers normalized title variants
only when release year or runtime independently corroborates the match. Media type is always enforced. Conflicting
identifiers and multiple viable candidates produce explicit ambiguous results; title-only or absent matches remain
unresolved.

Successful matches preserve non-conflicting source identifiers in the shared catalog, so later synchronization can use
the exact deterministic path. An optional provider-independent enrichment boundary can add normalized evidence for
sparse source records without coupling the matching algorithm to a metadata transport implementation.

### Netflix personal-data import

`NetflixFileImporter` parses local Netflix Viewing Activity CSV files and the documented supported ratings layout,
maps the external profile label to the internal default profile, and persists only safely resolved records. Import
reports distinguish new, repeated, unresolved, ambiguous, and invalid rows without echoing private titles. Stable
opaque source identities make re-import idempotent while preserving genuinely repeated watches.

No Netflix API key, PAT, credentials, cookies, or browser automation are used. Instructions for downloading exports,
supported columns and encodings, locale-specific date configuration, privacy, and optional metadata enrichment are in
[Netflix personal-data import](docs/netflix-import.md).

### Metadata provider development

External metadata integrations use a shared provider contract and asynchronous HTTP infrastructure. See
[Provider integration conventions](docs/provider-integrations.md) for lifecycle, validation, error handling, retry,
credential, mapping, offline testing requirements, and TMDB runtime configuration.

## License

License information will be added as the project structure is established.
