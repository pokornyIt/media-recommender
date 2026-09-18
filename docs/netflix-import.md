# Netflix personal-data import

Media Recommender imports Netflix history from local files. It does not use a Netflix API, log in to Netflix, read
browser cookies, or scrape account pages. Netflix discontinued its public API in 2014, and there is no supported
Netflix API key, personal access token (PAT), OAuth client, or similar credential to create for this integration.

## Obtain a viewing-history file

Netflix provides a CSV download separately for each profile:

1. Sign in to Netflix in a web browser.
2. Open **Account**.
3. Select the profile to import.
4. Open **Viewing activity**.
5. Select **Download all** at the bottom of the page.
6. Keep the downloaded `NetflixViewingHistory.csv` outside the source repository.
7. Choose a stable local label for that Netflix profile when invoking the importer. The label maps the external
   profile to Media Recommender's internal default profile; it does not need to be a secret or a Netflix identifier.

Netflix documents the profile-specific viewing activity and CSV download in its
[Viewing history help article](https://help.netflix.com/en/node/101917).

The normal file must contain `Title` and `Date` columns. Additional columns are ignored. The default date format is
the US-style `%m/%d/%y`; configure `NetflixCsvParser(date_format=...)` explicitly for another locale, for example
`%d.%m.%y`. UTF-8 (with or without a byte-order mark), UTF-16 with a byte-order mark, and Windows-1252 text are
supported.

The normal export identifies the selected profile through the download workflow rather than a CSV column. Always
pass the label of the profile from which the file was downloaded. Do not combine files from different Netflix
profiles before importing them.

## Obtain richer account data and ratings

Netflix also allows an account owner to request a copy of personal information at
[Get My Info](https://www.netflix.com/account/getmyinfo). Netflix says that the archive can include profile-scoped
content-interaction history, including rated titles; generation can take up to 30 days. See Netflix's
[personal-information help article](https://help.netflix.com/en/node/100624).

Media Recommender supports the `CONTENT_INTERACTION/Ratings.csv` layout:

| Column             | Required | Meaning                                                                    |
| ------------------ | -------- | -------------------------------------------------------------------------- |
| `Profile Name`     | yes      | Netflix profile label; rows must match the profile selected for import     |
| `Title Name`       | yes      | Title evidence passed to shared identity resolution                        |
| `Rating Type`      | yes      | Selects the documented star or thumb interpretation                        |
| `Star Value`       | yes      | Legacy star rating; positive values are retained on their original scale   |
| `Thumbs Value`     | yes      | `1` means thumbs down and `2` means thumbs up; `0` is not an active rating |
| `Event Utc Ts`     | no       | UTC timestamp of the interaction; ISO-like values are supported            |
| `Region View Date` | no       | Fallback date in the parser's configured date format                       |
| `Device Model`     | no       | Accepted when present but deliberately not retained                        |

Netflix may change the contents or names of files in a full account-data archive. Inspect the archive's own
documentation and headers before import. A file with different headers is not silently guessed into this format.

## Web import page

The Web UI provides a Netflix viewing-activity import page at `/imports/netflix`. It supports only the
profile-specific `NetflixViewingHistory.csv` export described above; the account-data `Ratings.csv` file is not
accepted through this page.

The form requires the CSV file and the stable Netflix profile label the export belongs to. Uploaded data is
request-transient: it is staged in a private temporary file only for the duration of the import request, is never
persisted, and is always removed afterwards. Filenames, profile labels, file contents, and temporary paths are never
rendered or logged. The result page shows only aggregate counts: imported, already imported (skipped), unresolved,
ambiguous, and invalid. Repeating an import of the same export is idempotent and reports every record as already
imported.

Uploads are limited to 10 MiB, enforced on the actual streamed bytes. Change the limit with
`MEDIA_RECOMMENDER_NETFLIX_IMPORT_MAX_UPLOAD_BYTES` (a positive integer byte count). Invalid, empty, oversized, or
unsupported uploads are rejected before any import work runs.

The page is protected with a signed-session CSRF token. The token is stored in an `HttpOnly`, `SameSite=Lax` cookie
(`mr_csrf`) signed with the runtime session secret and mirrored in a hidden form field that is compared in constant
time on submission. Set a stable, non-empty secret with `MEDIA_RECOMMENDER_WEB_SESSION_SECRET`; when it is unset, an
ephemeral per-process secret is generated, which invalidates issued form sessions on restart. Deploy the application
behind HTTPS so the session cookie is never sent over an untrusted network.

## Import behavior

`NetflixFileImporter` is the file-oriented integration entry point. It accepts a `pathlib.Path` and an external
profile label, parses the provider format into Netflix DTOs, and delegates normalized records to
`PersonalMediaImportService`. The application service then:

1. maps the Netflix profile label to the internal default profile;
2. resolves every title through the shared `MediaIdentityResolver` boundary;
3. persists only exact or safely resolved matches;
4. reports ambiguous, unresolved, and invalid rows without including their private titles;
5. uses opaque source hashes so an unchanged import is idempotent while repeated watches remain distinct.

A normal Viewing Activity export contains only a title and date. This can be insufficient for deterministic matching.
Configure identity enrichment or populate the shared catalog with enough external ID, media type, release-year, or
runtime evidence. The importer reports a sparse or ambiguous title instead of selecting an unsafe match.

The exported files, extracted archives, and derived databases are private runtime data. Do not commit them, copy them
into test fixtures, log their rows, or upload them as CI artifacts. Repository tests use only synthetic records.

## Credentials summary

- Netflix credential: none.
- Netflix API key or PAT: none exists for this supported workflow.
- Browser cookie or session export: never use one.
- Media Recommender profile label: caller-selected non-secret text used only for ownership mapping.
- Optional metadata-provider credential: configured separately from Netflix import; for example, TMDB setup is
  documented in [Provider integration conventions](provider-integrations.md#tmdb).
