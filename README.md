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

The application is built around a shared recommendation and domain layer.

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

## Main project areas

### Application Core

Defines the domain model, persistence, configuration, application services, and the foundation of
the recommendation engine.

### Media Sources and Metadata

Provides metadata about movies and TV shows, including genres, production countries, runtime, artwork,
external identifiers, and streaming availability.

TMDB is expected to be the initial metadata provider.

### Personal Media Data

Imports and synchronizes user-specific media information.

Initial sources are expected to include:

* Netflix viewing history and ratings where available;
* Jellyfin library contents and watched state.

Additional providers may be added later.

### Recommendations and AI

Provides deterministic filters, preferences, exclusions, scoring, and ranking.

AI support is optional and should primarily provide:

* natural-language interpretation;
* conversational refinement of recommendations;
* explanation of why a title was recommended.

Structured filtering and recommendation logic should not depend on an AI provider.

### Web Application

Provides the primary self-hosted user interface for browsing, filtering, configuring, and requesting recommendations.

### MCP Integration

Exposes selected application capabilities as MCP tools for clients such as Codex and, where supported, ChatGPT.

MCP is an interface to the application rather than the application core itself.

### Deployment and Operations

Provides a practical self-hosted deployment model including:

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

Media Recommender is currently in the early design and bootstrap stage.

Architecture, integrations, and implementation details may change significantly before the first stable release.

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
