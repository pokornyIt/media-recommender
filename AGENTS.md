# AGENTS.md

This file contains repository-wide instructions for AI coding agents and automated development tools.

## Communication

* Communicate with the maintainer in Czech when interacting conversationally.
* Write source code, comments, identifiers, documentation, commit messages, issue content, and other repository
  artifacts in English unless explicitly requested otherwise.
* Keep technical terminology consistent across the repository.

## Project purpose

Media Recommender is a self-hosted application that combines media metadata, streaming availability,
local media libraries, personal viewing history, ratings, preferences, and optional AI assistance to help users
decide what to watch.

The application must remain useful without an AI provider configured.

## Architecture principles

Follow these principles unless an issue explicitly requires otherwise:

* Keep the domain model independent of external providers.
* Integrations provide data; they must not own the core recommendation logic.
* Web, REST API, MCP, CLI, and other interfaces must reuse the same application services.
* MCP is an interface layer, not the application core.
* Keep provider-specific code isolated behind explicit interfaces.
* Prefer deterministic filtering and scoring for structured constraints.
* Use AI primarily for natural-language interpretation, conversational refinement, and explanation.
* Do not make basic recommendation functionality dependent on an external AI service.
* Keep personal user data and secrets outside the source repository.
* Keep shared media/catalog data separate from user-specific state.
* Personal history, ratings, preferences, exclusions, and provider-account mappings must have explicit
  internal ownership once introduced.
* Do not use an external provider identity such as Jellyfin or Netflix as the canonical application user identity.
* Multi-user profile management and authentication are future concerns; do not introduce unused authentication
  complexity before it is required.

Avoid duplicating business logic across interfaces or integrations.

## Development approach

Prefer small, reviewable changes.

Before implementing a change:

1. understand the relevant issue and existing architecture;
2. identify the smallest coherent change;
3. avoid unrelated refactoring;
4. preserve backwards compatibility unless a breaking change is intentional and documented.

Do not introduce abstractions merely because they may become useful later.

Prefer straightforward code over speculative extensibility.

## Python

When Python is used:

* Use modern Python and precise type annotations.
* Prefer `pathlib` over string-based filesystem manipulation.
* Prefer explicit domain models over loosely structured dictionaries for internal application data.
* Keep I/O and provider-specific behavior outside pure domain and recommendation logic where practical.
* Do not silently catch broad exceptions.
* Raise or translate errors at clear architectural boundaries.
* Do not log secrets, access tokens, API keys, personal viewing data, or sensitive provider responses.

Every Python function and method, including private and protected functions and methods, must have at least
a concise English docstring.

Use Sphinx-style structured docstrings when parameters, return values, or raised exceptions require
additional explanation.

Example:

```python
def find_movie(movie_id: int) -> Movie:
    """Return a movie from the local catalog.

    :param movie_id: Internal movie identifier.
    :return: Matching movie.
    :raises MovieNotFoundError: If the movie does not exist.
    """
```

Do not duplicate type annotations inside docstrings.

## Dependencies

Do not add a production dependency without a concrete need.

When adding one:

* explain why the standard library or an existing dependency is insufficient;
* prefer actively maintained and well-established libraries;
* keep runtime dependencies minimal;
* document significant dependency decisions in the change summary.

Development-only tooling may be added when it provides clear value to testing, linting, formatting, typing,
or maintenance.

## Data and privacy

Treat viewing history, ratings, provider credentials, tokens, API keys, and local databases as private runtime data.

Never:

* commit personal viewing history;
* commit real API keys or tokens;
* add credentials to examples;
* include real personal data in tests or fixtures.

Use clearly synthetic test data.

Configuration examples must use placeholders.

## External services

Do not rely on undocumented scraping when an official API, export, or supported integration path exists.

When integrating an external provider:

* keep its client implementation isolated;
* normalize provider data into the application's domain model;
* preserve external identifiers where useful for synchronization;
* handle rate limits and transient failures explicitly;
* make provider failure visible without unnecessarily breaking unrelated functionality.

Do not implement mechanisms intended to bypass access controls, DRM, geographic restrictions, or service terms.

## Recommendation logic

Structured user requirements such as these should be handled deterministically whenever possible:

* genre;
* runtime;
* production country or region;
* watched/unwatched state;
* availability;
* explicit exclusions;
* ratings;
* year ranges.

AI must not invent metadata that can be obtained from structured sources.

Where recommendation ranking combines multiple criteria, make the scoring understandable and testable.

A recommendation should be explainable in terms of known data and user preferences.

## AI integration

AI support is optional.

Keep AI-provider-specific implementation behind an abstraction so that the application core does not depend directly
on one model vendor.

Prompts must not contain secrets unnecessarily.

Where structured output is expected, validate model output before using it in application logic.

Do not use an LLM as a substitute for deterministic filtering, validation, database queries, or provider APIs.

## API and MCP

REST API and MCP tools should expose application capabilities rather than reimplement them.

Keep interfaces:

* narrow;
* typed where possible;
* documented;
* stable enough for independent clients.

Do not expose internal persistence details directly through public interfaces.

MCP tools should perform meaningful application actions and should not simply mirror every internal function.

## Web application

Keep business logic out of frontend components.

The frontend should consume application APIs rather than duplicating recommendation rules.

The application should remain usable on desktop and mobile-sized screens.

Accessibility should be considered part of normal implementation rather than a later optional enhancement.

## Persistence

Database schema changes must be handled through explicit migrations once persistent application data exists.

Do not require users to delete their database as a normal upgrade path.

Keep provider-specific raw payloads out of the primary domain schema unless there is a documented reason to retain them.

## Docker and deployment

The primary deployment target is Docker.

The application should support:

* persistent application data through mounted storage;
* configuration through documented environment variables or configuration files;
* secrets supplied at runtime;
* container health checks;
* graceful startup and shutdown.

Do not bake credentials or user-specific configuration into container images.

Prefer a simple single-host Docker Compose deployment before introducing orchestration-specific complexity.

## Testing

Add tests for new behavior and bug fixes where practical.

Prioritize tests for:

* domain logic;
* recommendation filters and scoring;
* data normalization;
* provider mapping;
* database migrations;
* API and MCP contracts.

Tests must not depend on live external services unless explicitly designed as integration tests.

Mock or fake provider boundaries rather than internal business logic.

Regression fixes should include a test that demonstrates the original failure whenever practical.

## Documentation

Update documentation when behavior, configuration, architecture, deployment, or public interfaces change.

Keep documentation consistent with actual behavior.

Do not document planned functionality as if it already exists.

Use concise Markdown and keep headings and terminology consistent.

## Tooling

Prefer repository-defined tooling and commands over ad-hoc alternatives.

Once project tooling exists, run the relevant checks before considering a change complete.

Expected categories include:

* formatting;
* linting;
* type checking;
* documentation validation;
* tests.

Do not disable or weaken checks merely to make a change pass without explaining the reason.

## Change quality

Before finishing a change:

* remove unused code;
* remove temporary debugging output;
* verify error handling;
* check that secrets cannot leak into logs;
* update tests;
* update documentation when needed;
* verify that unrelated behavior has not been changed accidentally.

Keep commits and pull requests focused on one coherent purpose.
