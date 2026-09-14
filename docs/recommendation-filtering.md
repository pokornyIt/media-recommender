# Deterministic recommendation filtering

`RecommendationFilterService` turns typed `RecommendationCriteria` into a reproducible candidate set. It consumes
normalized catalog, profile, local-library, and streaming-availability facts through `RecommendationDataSource`; it
does not call an AI provider or contain ranking weights.

## Hard-constraint semantics

All configured criteria are hard constraints. Genre inclusion uses all-of semantics by default and can explicitly use
any-of semantics. Country, broad production region, runtime, release year/date, personal rating, reaction, media type,
and profile-specific watch state can be included or excluded as applicable. Bounds are inclusive.

Broad production regions are derived deterministically from stored ISO 3166-1 alpha-2 production-country codes. The
supported groups are Africa, Asia, Europe, North America, South America, and Oceania. Unrecognized codes are not
silently assigned to a region.

Missing metadata fails a criterion that requires a known positive match or range value. For example, a title with no
runtime is excluded when a maximum runtime is requested. Missing metadata does not by itself match an explicit
exclusion such as an excluded country.

Watch requirements preserve the existing three-state personal model:

* `WATCHED` requires positive viewing evidence;
* `UNWATCHED` requires an explicit unwatched state;
* `NOT_WATCHED` accepts explicit unwatched and unknown state but rejects watched state;
* `ANY` does not constrain watch state.

This distinction lets a caller express either strict provider-reported unwatched state or the common recommendation
meaning of “no evidence that I watched this.”

## Availability and preferences

Entries in `availability_any_of` are alternatives. A request such as Netflix subscription availability in `CZ` or
presence in the profile's Jellyfin library is accepted when either normalized fact exists. Streaming requirements may
restrict the availability-data source, service, region, and monetization type. Local-library requirements remain
profile-specific and separate from shared streaming availability.

Persisted profile criteria with effect `EXCLUDE` are applied by default. Criteria with effect `PREFER` are deliberately
ignored by filtering and remain inputs for the later ranking layer. Callers can disable persisted exclusions explicitly
when they need to inspect only request-local constraints.

## Results and persistence

The result contains deterministically ordered accepted media plus one decision for every loaded candidate. Rejected
candidates carry typed `FilterExclusion` values so later explanations and diagnostics do not need to reconstruct or
invent facts.

`SqlAlchemyRecommendationDataSource` loads media and related profile/shared facts in a constant number of queries. It
does not issue one query per catalog item, which keeps the SQLite workflow suitable for expected self-hosted catalogs.

## Ranking and explanations

`RecommendationService` evaluates filtering and ranking against one immutable candidate snapshot. Rejected candidates
remain available through the filter decisions, but only accepted candidates reach ranking, so preferences cannot
weaken a hard constraint.

The default score uses explicit profile information only:

* each matching persisted `PREFER` criterion contributes 100 points;
* each point of the highest known personal rating contributes 5 points;
* an explicit like contributes 30 points;
* an explicit dislike subtracts 30 points.

`RankingWeights` can replace these non-negative integer magnitudes. Equal scores are ordered by case-insensitive title
and then stable media identity. With no scoring signals, this tie-break is the complete default ranking behavior.

Every ranked result contains the score, ordinal rank, individual signed score contributions, matched configured hard
constraints, known local and streaming availability, profile-specific watch state, personal rating/reactions, and
typed warnings for unknown runtime, release date, watch state, rating, or availability. These facts are derived from
normalized application data and are not AI-generated prose. Missing genres and production countries are also reported
explicitly.
