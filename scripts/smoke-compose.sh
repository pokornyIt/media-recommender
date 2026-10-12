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

printf 'Compose smoke test passed.\n'
