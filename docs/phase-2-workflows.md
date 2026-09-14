# Phase 2 application workflows

Phase 2 combines personal Netflix data, a Jellyfin library, shared regional streaming availability, deterministic
identity resolution, filtering, and ranking. `Phase2Orchestrator` is the in-process facade intended for later API and
Web layers. It composes existing application and integration boundaries; it does not write provider payloads directly
to persistence or require background-job infrastructure.

There is no end-user synchronization UI yet. Callers must construct the configured integrations and application
services explicitly.

## Ownership and supported sources

Personal state belongs to the deterministic internal default profile returned by `ProfileRepository`. Netflix profile
labels and the configured Jellyfin user are external mappings to that owner, not application identities. Shared
catalog metadata and regional streaming availability do not belong to a profile.

Supported Phase 2 inputs are:

* local Netflix Viewing Activity and supported account-data ratings CSV files;
* the selected Jellyfin user's movies, series, playback state, and library presence through the supported API;
* regional watch-provider availability retrieved from TMDB, including Netflix in `CZ` when reported upstream.

Netflix files and the resulting database contain private personal data. Keep them outside the repository, logs, test
artifacts, and any shared upload. Netflix requires no credential because imports read local exports. Jellyfin and TMDB
credentials are supplied only through the runtime environment described in
[Provider integration conventions](provider-integrations.md).

## Synchronization contract

`Phase2SynchronizationRequest` selects optional Netflix files and regional availability requests. Jellyfin uses its
configured selected user. `Phase2Orchestrator.synchronize()` runs operations in stable order and returns one
`WorkflowReport` per configured source, with normalized counts for successful, skipped, unresolved, ambiguous,
invalid, failed, and removed records.

Imports and complete snapshots are safe to repeat:

* Netflix source hashes prevent equivalent viewing or rating rows from being inserted twice;
* Jellyfin source item identities update existing library and watch state;
* successful availability refreshes atomically replace the selected media, region, and source snapshot.

Provider failures are reported with a sanitized failure classification and do not stop unrelated configured sources.
A failed Jellyfin retrieval happens before its complete snapshot is applied. A failed availability retrieval happens
before its previous snapshot is replaced. Consequently, transient failures preserve the last valid local state. A
successful empty availability response is different: it is a valid complete snapshot and removes obsolete offers.

Availability is a last-observed upstream fact, not a real-time entitlement guarantee. Results retain their observation
timestamp and attribution, and callers should expose staleness rather than infer current access.

## Identity diagnostics

All sources resolve media through the shared deterministic identity service. Exact external IDs take precedence;
metadata fallback requires corroborating evidence. Ambiguous, unresolved, malformed, and missing-source-ID records
remain in workflow reports with safe reasons and candidate IDs where known. They are not silently attached to catalog
items and therefore cannot corrupt viewing, library, availability, or recommendation state. Manual resolution is a
future interface concern.

## Recommendations

`Phase2Orchestrator.recommend()` resolves the active implicit default profile and delegates to `RecommendationService`.
Hard constraints run before ranking. Ranking uses explicit profile preferences, ratings, and reactions with documented
integer weights and stable tie-breaking. The result includes factual constraint matches, signed score contributions,
known availability, profile watch/rating state, and typed missing-data warnings. No AI provider is required. Detailed
filtering and ranking semantics are documented in [Deterministic recommendation filtering](recommendation-filtering.md).

## Migrations and validation

Alembic upgrades an existing Phase 1 revision through the personal data, rating timestamp, Jellyfin presence, and
streaming availability revisions without deleting the shared catalog. Always upgrade before running Phase 2 services:

```bash
uv run alembic upgrade head
```

The canonical complete validation is fully offline and uses synthetic provider inputs and temporary databases:

```bash
uv sync --all-groups --frozen
uv run pre-commit run --all-files
```

Individual checks remain available for focused development:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pydoclint src
uv run pytest
```
