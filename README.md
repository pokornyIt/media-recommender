# Media Recommender

Media Recommender is a self-hosted application for discovering what to watch by combining personal viewing history,
ratings, preferences, media metadata, streaming availability, and local media libraries.

The project is designed to work as a standalone web application while exposing the same underlying capabilities
through an API and MCP.

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
* optional AI-assisted natural-language interaction;
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

The FastAPI HTTP boundary and server-rendered Web shell are implemented; provider settings and configuration UI,
import and synchronization UI or controls, complete recommendation and media-detail screens, MCP, and deployment
remain planned. Local Netflix Viewing Activity
and supported ratings CSV imports now flow through the shared identity resolver into
profile-owned personal state. Jellyfin movie and series libraries can be synchronized through its supported API,
including distinct library presence and per-user watched state. Regional streaming availability can be refreshed from
TMDB watch-provider data and is stored separately from both personal state and local-library presence. Typed hard
constraints now combine those normalized facts into deterministic profile-specific recommendation candidate sets.
The in-process Phase 2 orchestration facade coordinates source workflows and deterministic ranked recommendations for
the implicit default profile while returning structured summaries suitable for future interfaces.

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
`media_recommender.web.create_app()`. The factory only initializes interface concerns and does not construct
repositories, providers, or application services. Feature routes define their own FastAPI `Depends` dependencies;
composition roots and tests can use the application's native `dependency_overrides` mapping.

Versioned business API routes are available under `/api/v1`. `GET /health/live` is versionless and verifies only that
the HTTP process can serve requests: it does not query the database or call providers. `GET /api/v1/media/{media_id}`
retrieves normalized shared catalog metadata through the injected `CatalogService`; it returns movie or TV-show facts,
but never profile-owned state, streaming availability, library presence, provider transport data, or ORM records. The
shared page layout and static CSS form the responsive, accessible baseline for later server-rendered screens.

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
failure, transient provider failure, partial results, and success, and never renders provider URLs, tokens, external
identities, item data, or raw errors. A failed refresh does not affect `/settings`, `/providers/status`,
`/imports/netflix`, or `/synchronizations/jellyfin`, and a repeat submission is an explicit new snapshot rather than an
automatic retry.

The implemented server-rendered UI includes the shared application shell, home page, read-only provider status page,
the Netflix Viewing Activity import page, the Jellyfin library synchronization page, and the regional
streaming-availability refresh page. Provider configuration editing, recommendation results, and media-detail and
personal-state screens remain future Web work.

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

Planned; not implemented yet. Docker images, Docker Compose, health checks, and release packaging are not
implemented yet.
The intended deployment scope includes:

* Docker images;
* Docker Compose;
* persistent storage;
* configuration and secrets;
* health checks;
* database migrations;
* upgrade documentation.

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

The repository provides a FastAPI application factory, a server-rendered application shell, and documented liveness,
media-read, and deterministic recommendation HTTP endpoints. There is no production ASGI entry point, complete
end-user workflow UI, AI behavior, MCP interface, or production deployment yet. Phase 2 recommendation and
synchronization capabilities are exposed as in-process application services for future interfaces.
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

Creating an application database engine or session does not create or recreate tables. Normal application startup is
therefore expected to fail clearly when migrations have not been applied, rather than silently changing the schema.

### Catalog application service

`CatalogService` is the provider-independent entry point for catalog workflows currently used by the REST interface,
including `GET /api/v1/media/{media_id}`; future Web and MCP interfaces can reuse it. It searches explicitly selected
configured metadata providers, synchronizes normalized detail into the
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
