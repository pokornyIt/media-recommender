#!/usr/bin/env bash

# Build and exercise the production Compose deployment without touching a developer deployment.
set -Eeuo pipefail

readonly repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly project_name="media-recommender-smoke-${$}-${RANDOM}"
readonly temporary_directory="$(mktemp -d)"
readonly environment_file="${temporary_directory}/environment"
readonly override_file="${temporary_directory}/compose.smoke.yaml"
readonly image_tag="media-recommender:smoke-${$}-${RANDOM}"

phase="preparing"
built_image=false
compose_configured=false
compose_command=()

compose() {
  if [[ "${compose_configured}" != true ]]; then
    return 0
  fi
  "${compose_command[@]}" "$@"
}

cleanup() {
  local cleanup_failed=false

  if ! compose down --volumes --remove-orphans; then
    printf 'Smoke-test cleanup could not remove Compose resources for %s.\n' "${project_name}" >&2
    cleanup_failed=true
  fi

  if [[ "${built_image}" == true ]] && ! docker image rm "${image_tag}" >/dev/null; then
    printf 'Smoke-test cleanup could not remove image %s.\n' "${image_tag}" >&2
    cleanup_failed=true
  fi

  if ! rm -rf -- "${temporary_directory}"; then
    printf 'Smoke-test cleanup could not remove its temporary directory.\n' >&2
    cleanup_failed=true
  fi

  [[ "${cleanup_failed}" == false ]]
}

diagnose_failure() {
  printf 'Compose smoke test failed during %s.\n' "${phase}" >&2
  compose ps >&2 || true
  compose logs --tail=100 --no-color >&2 || true
}

on_exit() {
  local status=$?

  trap - EXIT
  if (( status != 0 )); then
    diagnose_failure
  fi
  if ! cleanup; then
    status=1
  fi
  exit "${status}"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! command -v docker >/dev/null; then
  printf 'Docker is required for the Compose smoke test.\n' >&2
  exit 1
fi
if ! docker info >/dev/null; then
  printf 'Docker daemon is unavailable; start it or grant this user Docker access.\n' >&2
  exit 1
fi
if ! docker compose version >/dev/null; then
  printf 'Docker Compose v2 is required for the Compose smoke test.\n' >&2
  exit 1
fi

compose_command=(
  docker compose
  --project-name "${project_name}"
  --env-file "${environment_file}"
  --file "${repository_root}/compose.yaml"
  --file "${override_file}"
)
compose_configured=true

touch "${environment_file}"
cat >"${override_file}" <<EOF
services:
  media-recommender:
    image: ${image_tag}
    restart: "no"
    ports: !reset []
    networks:
      smoke:

networks:
  smoke:
    internal: true
EOF

export MEDIA_RECOMMENDER_DEFAULT_REGION=CZ
export MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN=
export MEDIA_RECOMMENDER_JELLYFIN_BASE_URL=
export MEDIA_RECOMMENDER_JELLYFIN_USER_ID=
export MEDIA_RECOMMENDER_LOG_LEVEL=WARNING
export MEDIA_RECOMMENDER_TMDB_API_TOKEN=
export MEDIA_RECOMMENDER_WEB_SESSION_SECRET
MEDIA_RECOMMENDER_WEB_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

phase="building the production image"
compose build
built_image=true

phase="starting the Compose service"
compose up --detach --wait --wait-timeout 120

phase="checking HTTP responses"
compose exec -T media-recommender python - <<'PY'
import json
from urllib.request import urlopen


def response_body(path: str) -> bytes:
    """Return a successful local HTTP response body."""
    with urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return response.read()


ready = json.loads(response_body("/health/ready"))
expected_readiness = {"status": "ready", "database": "ok", "migrations": "current"}
if ready != expected_readiness:
    raise RuntimeError(f"unexpected readiness response: {ready!r}")

home = response_body("/").decode("utf-8")
if "<h1>Media Recommender</h1>" not in home:
    raise RuntimeError("home page did not contain its expected heading")
PY

phase="preparing synthetic persisted state"
compose exec -T media-recommender python - <<'PY'
"""Store one synthetic catalog record through the production repository."""
import asyncio
from datetime import date
from uuid import UUID

from media_recommender.config import Settings
from media_recommender.domain import Country, ExternalId, Genre, MediaId, Movie, Runtime
from media_recommender.persistence.database import create_engine, create_session_factory
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog


async def prepare() -> None:
    """Persist deterministic state in the mounted smoke-test volume."""
    engine = create_engine(Settings())
    try:
        catalog = SqlAlchemyMediaCatalog(create_session_factory(engine))
        await catalog.save(Movie(
            id=MediaId(UUID("00000000-0000-4000-8000-000000000046")),
            title="Synthetic Smoke Movie", released_on=date(2026, 1, 1), runtime=Runtime(100),
            genres=(Genre("Science Fiction"),), production_countries=(Country("CZ", "Czechia"),),
            external_ids=frozenset({ExternalId("tmdb", "smoke-46")}),
        ))
    finally:
        await engine.dispose()


asyncio.run(prepare())
PY

phase="backing up populated state"
compose exec -T media-recommender python - <<'PY'
"""Create and validate the documented online SQLite backup."""
import sqlite3

source = sqlite3.connect("/data/media-recommender.db")
destination = sqlite3.connect("/data/smoke-backup.db")
try:
    source.backup(destination)
finally:
    destination.close()
    source.close()
with sqlite3.connect("/data/smoke-backup.db") as connection:
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RuntimeError("backup integrity check failed")
PY

phase="recreating the populated service"
compose up --detach --force-recreate --wait --wait-timeout 120

phase="checking persisted HTTP state"
compose exec -T media-recommender python - <<'PY'
"""Verify readiness and synthetic state through the deployed HTTP API."""
import json
from urllib.request import urlopen

with urlopen("http://127.0.0.1:8000/health/ready", timeout=5) as response:  # noqa: S310
    if json.loads(response.read()) != {"status": "ready", "database": "ok", "migrations": "current"}:
        raise RuntimeError("service was not ready after recreation")
with urlopen("http://127.0.0.1:8000/api/v1/media/00000000-0000-4000-8000-000000000046", timeout=5) as response:  # noqa: S310
    if "Synthetic Smoke Movie" not in response.read().decode("utf-8"):
        raise RuntimeError("persisted media was not available after recreation")
PY

phase="restoring populated state"
compose exec -T media-recommender python - <<'PY'
"""Change the backed-up record through the production repository."""
import asyncio
from datetime import date
from uuid import UUID

from media_recommender.config import Settings
from media_recommender.domain import Country, ExternalId, Genre, MediaId, Movie, Runtime
from media_recommender.persistence.database import create_engine, create_session_factory
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog


async def mutate() -> None:
    """Overwrite the synthetic record so restore has an observable effect."""
    engine = create_engine(Settings())
    try:
        catalog = SqlAlchemyMediaCatalog(create_session_factory(engine))
        await catalog.save(Movie(
            id=MediaId(UUID("00000000-0000-4000-8000-000000000046")),
            title="Changed Smoke Movie", released_on=date(2026, 1, 1), runtime=Runtime(100),
            genres=(Genre("Science Fiction"),), production_countries=(Country("CZ", "Czechia"),),
            external_ids=frozenset({ExternalId("tmdb", "smoke-46")}),
        ))
    finally:
        await engine.dispose()


asyncio.run(mutate())
PY
compose stop
compose run --rm --no-deps --entrypoint python media-recommender -c \
  "from pathlib import Path; from media_recommender.persistence.migrations import restore_database; restore_database(Path('/data/smoke-backup.db'), Path('/data/media-recommender.db'))"
compose up --detach --wait --wait-timeout 120

phase="checking restored state"
compose exec -T media-recommender python - <<'PY'
"""Verify schema, integrity, and restored HTTP facts after recovery."""
import json
import sqlite3
from urllib.request import urlopen

with sqlite3.connect("/data/media-recommender.db") as connection:
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RuntimeError("restored database integrity check failed")
with urlopen("http://127.0.0.1:8000/health/ready", timeout=5) as response:  # noqa: S310
    if json.loads(response.read()) != {"status": "ready", "database": "ok", "migrations": "current"}:
        raise RuntimeError("service was not ready after restore")
with urlopen("http://127.0.0.1:8000/api/v1/media/00000000-0000-4000-8000-000000000046", timeout=5) as response:  # noqa: S310
    if "Synthetic Smoke Movie" not in response.read().decode("utf-8"):
        raise RuntimeError("restore did not recover the backed-up HTTP state")
PY

printf 'Compose smoke test passed.\n'
