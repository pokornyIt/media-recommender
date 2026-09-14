# Media Recommender

Media Recommender is a self-hosted application for discovering what to watch by combining personal viewing history,
ratings, preferences, media metadata, streaming availability, and local media libraries.

The project is designed to work as a standalone web application while exposing the same underlying capabilities
through an API and MCP.

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
* streaming availability for a selected region;
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

The Web, REST, MCP, recommendation, provider-import, availability, and deployment layers shown above remain planned.

## Main project areas

### Application Core

Defines typed movie and TV-show domain models, configuration, persistence contracts, and catalog application services.

### Media Sources and Metadata

TMDB currently provides normalized movie and TV-show search and detail metadata, including genres, production
countries, runtime, artwork, release information, and external identifiers. Streaming availability is not implemented.

### Personal Media Data

The provider-independent domain and SQLite persistence layers can store profile-owned viewing history, ratings,
explicit like/dislike state, preferences, exclusions, and provider-profile mappings. Viewing history is event-based,
so repeated watches remain distinct. Watch status preserves `watched`, explicit `unwatched`, and `unknown` as separate
states. A rating does not implicitly mark an item as watched, and missing records continue to represent unknown state.

The initial single-user workflow uses one deterministic implicit default profile. Profile management, switching,
authentication, and authorization are not implemented.

Initial sources are expected to include:

* Netflix viewing history and ratings where available;
* Jellyfin library contents and watched state.

Additional providers may be added later.

### Recommendations and AI

Planned after Phase 1. No recommendation filters, scoring, ranking, or AI integration currently exists.

AI support is optional and should primarily provide:

* natural-language interpretation;
* conversational refinement of recommendations;
* explanation of why a title was recommended.

Structured filtering and recommendation logic should not depend on an AI provider.

### Web Application

Planned after Phase 1. The repository does not currently expose a Web UI or public REST API.

### MCP Integration

Planned after Phase 1. The repository does not currently expose MCP tools.

MCP is an interface to the application rather than the application core itself.

### Deployment and Operations

Planned after Phase 1. Docker images, Docker Compose, health checks, and release packaging are not implemented yet.
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

There is no executable application entry point, end-user interface, recommendation engine, provider-specific
personal-media import, streaming-availability integration, AI behavior, MCP interface, or production deployment yet.
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

The Phase 1 stack uses Python 3.14, uv, Pydantic and pydantic-settings, HTTPX, SQLAlchemy 2.x with aiosqlite, Alembic,
pytest, Ruff, Pyright, pydoclint, and pre-commit. FastAPI is reserved for a future public API and is not imported by the
current application core.

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

Optional TMDB locale, endpoint, image, and timeout settings are documented in
[Provider integration conventions](docs/provider-integrations.md).

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

`CatalogService` is the provider-independent entry point for catalog workflows used by future Web, REST, and MCP
interfaces. It searches explicitly selected configured metadata providers, synchronizes normalized detail into the
catalog, and retrieves persisted media by internal or external identity. A refresh preserves the internal application
identity while replacing catalog metadata with the provider's latest normalized detail; missing detail fields clear
previously stored values instead of retaining stale metadata.

Each repository write owns one database transaction. Provider calls complete before that transaction starts, and a
persistence error rolls back the complete write. Provider failures and persistence failures are propagated for an
interface layer to translate, while missing providers, missing details, and mismatched provider results use explicit
application-service errors. External-ID conflicts are never resolved through title/year heuristics.

### Metadata provider development

External metadata integrations use a shared provider contract and asynchronous HTTP infrastructure. See
[Provider integration conventions](docs/provider-integrations.md) for lifecycle, validation, error handling, retry,
credential, mapping, offline testing requirements, and TMDB runtime configuration.

## License

License information will be added as the project structure is established.
