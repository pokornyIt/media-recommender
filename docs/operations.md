# Operations

This document describes the supported single-host runtime lifecycle for the Docker/Compose deployment: startup
ordering, health semantics, the explicit upgrade path, SQLite backup and restore, logging, and provider-outage
behavior.

## Startup ordering

1. The container starts `uvicorn media_recommender.asgi:app`.
2. The application lifespan configures stdout logging.
3. The lifespan applies pending Alembic migrations in a worker thread.
4. The lifespan builds the database engine, repositories, application services, and configured provider integrations.
5. Services are attached to application state and the application starts serving requests.
6. On `SIGTERM`, uvicorn stops accepting requests, then the lifespan closes provider HTTP clients and disposes the
   database engine.

Migrations run in a worker thread because the Alembic environment owns its own event loop. A migration failure aborts
startup, so the application never serves an incompatible schema.

## Health endpoints

| Endpoint                | Meaning                                                | Status codes                 |
| ----------------------- | ------------------------------------------------------ | ---------------------------- |
| `GET /health/live`      | Process liveness only; no database or provider access. | `200`                        |
| `GET /health/ready`     | Database accessibility and migration state.            | `200` ready, `503` not ready |
| `GET /health/providers` | Safe provider configuration and operational state.     | `200` always                 |

`/health/ready` reports `database` as `ok` or `unavailable` and `migrations` as `current`, `pending`, or `unknown`.
It never contacts an external provider, so a provider outage cannot make the application report itself as not ready.

`/health/providers` reports only safe configuration and operational facts. It never makes the application unhealthy
because an external service is temporarily unavailable.

The image healthcheck uses `GET /health/ready`.

## Upgrade path

Schema upgrades are explicit and migration-driven. Normal upgrades require no manual step:

1. Pull or build the new image.
2. Recreate the container against the same `/data` volume.
3. Startup applies all pending migrations before serving.

Before applying pending migrations to an existing database, the application creates a consistent snapshot with the
SQLite online backup API under `<database_dir>/backups/` and verifies it with `PRAGMA integrity_check`. If the backup
cannot be created or verified, the migration is not attempted. The five most recent automatic pre-upgrade backups are
retained, and the newest backup is always kept.

If a migration fails, the application logs a sanitized error and exits without serving. The pre-upgrade backup remains
available for recovery.

## Recovery after a failed migration

1. Stop the container.
2. Restore the verified pre-upgrade backup over the database path.
3. Verify the restored database.
4. Start the previous image version.

The restore helper is available in the image and verifies both the backup and the restored database:

```bash
docker compose stop media-recommender
docker compose run --rm --entrypoint python media-recommender -c \
  "from pathlib import Path; from media_recommender.persistence.migrations import restore_database; \
restore_database(Path('/data/backups/<backup-file>.db'), Path('/data/media-recommender.db'))"
docker compose up -d
```

## Backup

Use the SQLite online backup API rather than copying a live database file. A plain file copy can capture an
inconsistent state while the database is in use.

Create a consistent backup while the container is running:

```bash
docker compose exec media-recommender python -c \
  "import sqlite3; s=sqlite3.connect('/data/media-recommender.db'); d=sqlite3.connect('/data/manual-backup.db'); \
s.backup(d); d.close(); s.close()"
```

Verify the backup:

```bash
docker compose exec media-recommender python -c \
  "import sqlite3; print(sqlite3.connect('/data/manual-backup.db').execute('PRAGMA integrity_check').fetchone())"
```

The command must print `('ok',)`. Copy the verified backup out of the volume:

```bash
docker compose cp media-recommender:/data/manual-backup.db ./media-recommender-backup.db
```

## Restore

1. Stop the container.
2. Restore the verified backup over the database path.
3. Start the container.

```bash
docker compose stop media-recommender
docker compose run --rm --entrypoint python media-recommender -c \
  "from pathlib import Path; from media_recommender.persistence.migrations import restore_database; \
restore_database(Path('/data/manual-backup.db'), Path('/data/media-recommender.db'))"
docker compose up -d
```

## Logging

Application logs are written to stdout at `MEDIA_RECOMMENDER_LOG_LEVEL` (default `INFO`). Logs never include
credentials, provider tokens, external identities, or personal viewing history. Migration logs contain only revision
identifiers.

## Provider outages

Provider health is reported separately at `/health/providers` and never affects liveness or readiness. A provider
outage does not prevent `/`, `/settings`, `/providers/status`, or the health endpoints from working.

Workflow pages render a safe aggregate outcome instead of a server error: absent or invalid configuration,
authentication failure, transient failure, partial completion, or failure. An unconfigured provider is reported as
`not_configured` in workflow results and as `not_configured` or `configuration_error` in provider health.

## Rollback expectations

The initial release line supports forward migrations only; there is no automatic downgrade. To roll back an
application version, restore the pre-upgrade backup and start the previous image. Keep the pre-upgrade backup until the
new version has been verified.
