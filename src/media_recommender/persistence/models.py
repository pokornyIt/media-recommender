"""SQLAlchemy ORM models for shared catalog and profile-owned personal data."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MEDIA_ID_LENGTH: Final = 36
MEDIA_TYPE_LENGTH: Final = 20
TITLE_LENGTH: Final = 500
GENRE_NAME_LENGTH: Final = 100
COUNTRY_CODE_LENGTH: Final = 2
COUNTRY_NAME_LENGTH: Final = 100
NAMESPACE_LENGTH: Final = 100
EXTERNAL_ID_LENGTH: Final = 255
ARTWORK_TYPE_LENGTH: Final = 20
ARTWORK_URL_LENGTH: Final = 2_048
LANGUAGE_LENGTH: Final = 35
PROFILE_NAME_LENGTH: Final = 200
PROVIDER_LENGTH: Final = 100
SOURCE_RECORD_ID_LENGTH: Final = 500
SYNCHRONIZATION_ID_LENGTH: Final = 255
TIMESTAMP_LENGTH: Final = 40
PREFERENCE_KIND_LENGTH: Final = 30
PREFERENCE_EFFECT_LENGTH: Final = 10
PREFERENCE_VALUE_LENGTH: Final = 255
EXTERNAL_PROFILE_ID_LENGTH: Final = 500


class Base(DeclarativeBase):
    """Declarative base for persistence-layer ORM models."""


media_genres = Table(
    "media_genres",
    Base.metadata,
    Column("media_id", String(MEDIA_ID_LENGTH), ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_name", String(GENRE_NAME_LENGTH), ForeignKey("genres.name"), primary_key=True),
)

media_countries = Table(
    "media_countries",
    Base.metadata,
    Column("media_id", String(MEDIA_ID_LENGTH), ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True),
    Column("country_code", String(COUNTRY_CODE_LENGTH), ForeignKey("countries.code"), primary_key=True),
)


class MediaRecord(Base):
    """ORM representation of a shared movie or TV-show catalog item."""

    __tablename__ = "media_items"
    __table_args__ = (
        CheckConstraint("media_type IN ('movie', 'tv_show')", name="ck_media_items_type"),
        CheckConstraint("runtime_minutes IS NULL OR runtime_minutes > 0", name="ck_media_items_runtime_positive"),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(MEDIA_TYPE_LENGTH), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(TITLE_LENGTH), nullable=False, index=True)
    original_title: Mapped[str | None] = mapped_column(String(TITLE_LENGTH))
    release_date: Mapped[date | None] = mapped_column(Date)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer)

    genres: Mapped[list[GenreRecord]] = relationship(
        secondary=media_genres,
        order_by="GenreRecord.name",
        lazy="raise",
    )
    countries: Mapped[list[CountryRecord]] = relationship(
        secondary=media_countries,
        order_by="CountryRecord.code",
        lazy="raise",
    )
    artwork: Mapped[list[ArtworkRecord]] = relationship(
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="ArtworkRecord.id",
        lazy="raise",
    )
    external_ids: Mapped[list[ExternalIdRecord]] = relationship(
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="ExternalIdRecord.namespace",
        lazy="raise",
    )


class GenreRecord(Base):
    """ORM representation of a normalized genre."""

    __tablename__ = "genres"

    name: Mapped[str] = mapped_column(String(GENRE_NAME_LENGTH), primary_key=True)


class CountryRecord(Base):
    """ORM representation of an ISO production country."""

    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(COUNTRY_CODE_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(COUNTRY_NAME_LENGTH), nullable=False)


class ArtworkRecord(Base):
    """ORM representation of an artwork reference."""

    __tablename__ = "artwork"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH),
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artwork_type: Mapped[str] = mapped_column(String(ARTWORK_TYPE_LENGTH), nullable=False)
    url: Mapped[str] = mapped_column(String(ARTWORK_URL_LENGTH), nullable=False)
    language: Mapped[str | None] = mapped_column(String(LANGUAGE_LENGTH))

    media: Mapped[MediaRecord] = relationship(back_populates="artwork")


class ExternalIdRecord(Base):
    """ORM representation of a provider-namespaced external identity."""

    __tablename__ = "external_ids"
    __table_args__ = (
        UniqueConstraint("namespace", "value", name="uq_external_ids_namespace_value"),
        UniqueConstraint("media_id", "namespace", name="uq_external_ids_media_namespace"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH),
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(NAMESPACE_LENGTH), nullable=False)
    value: Mapped[str] = mapped_column(String(EXTERNAL_ID_LENGTH), nullable=False)

    media: Mapped[MediaRecord] = relationship(back_populates="external_ids")


class ProfileRecord(Base):
    """ORM representation of an internal personal-data owner."""

    __tablename__ = "profiles"
    __table_args__ = (
        Index("uq_profiles_single_default", "is_default", unique=True, sqlite_where=text("is_default = 1")),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(PROFILE_NAME_LENGTH), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ViewingEventRecord(Base):
    """ORM representation of one profile-owned watch event."""

    __tablename__ = "viewing_events"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "source_provider",
            "source_record_id",
            name="uq_viewing_events_source_record",
        ),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    watched_at: Mapped[str] = mapped_column(String(TIMESTAMP_LENGTH), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(SOURCE_RECORD_ID_LENGTH))
    synchronization_id: Mapped[str | None] = mapped_column(String(SYNCHRONIZATION_ID_LENGTH))
    imported_at: Mapped[str] = mapped_column(String(TIMESTAMP_LENGTH), nullable=False)


class WatchStateRecord(Base):
    """ORM representation of explicit provider-supplied watch state."""

    __tablename__ = "watch_states"
    __table_args__ = (
        CheckConstraint("status IN ('watched', 'unwatched')", name="ck_watch_states_status"),
        UniqueConstraint(
            "profile_id",
            "media_id",
            "source_provider",
            name="uq_watch_states_profile_media_provider",
        ),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(SOURCE_RECORD_ID_LENGTH))
    synchronization_id: Mapped[str | None] = mapped_column(String(SYNCHRONIZATION_ID_LENGTH))
    imported_at: Mapped[str] = mapped_column(String(TIMESTAMP_LENGTH), nullable=False)


class RatingRecord(Base):
    """ORM representation of a profile-owned rating or reaction."""

    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("value IS NULL OR (value >= 0 AND value <= 10)", name="ck_ratings_value_range"),
        CheckConstraint("like_state IS NULL OR like_state IN ('liked', 'disliked')", name="ck_ratings_like_state"),
        CheckConstraint("value IS NOT NULL OR like_state IS NOT NULL", name="ck_ratings_has_value"),
        UniqueConstraint("profile_id", "source_provider", "source_record_id", name="uq_ratings_source_record"),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[float | None] = mapped_column(Float)
    like_state: Mapped[str | None] = mapped_column(String(10))
    rated_at: Mapped[str | None] = mapped_column(String(TIMESTAMP_LENGTH))
    source_provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(SOURCE_RECORD_ID_LENGTH))
    synchronization_id: Mapped[str | None] = mapped_column(String(SYNCHRONIZATION_ID_LENGTH))
    imported_at: Mapped[str] = mapped_column(String(TIMESTAMP_LENGTH), nullable=False)


class PreferenceRecord(Base):
    """ORM representation of a profile-owned preference or exclusion."""

    __tablename__ = "preferences"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('genre', 'production_country', 'production_region', "
            "'runtime_minutes', 'release_year', 'provider')",
            name="ck_preferences_kind",
        ),
        CheckConstraint("effect IN ('prefer', 'exclude')", name="ck_preferences_effect"),
        CheckConstraint("minimum IS NULL OR minimum >= 0", name="ck_preferences_minimum_nonnegative"),
        CheckConstraint("maximum IS NULL OR maximum >= 0", name="ck_preferences_maximum_nonnegative"),
        CheckConstraint("minimum IS NULL OR maximum IS NULL OR minimum <= maximum", name="ck_preferences_bounds_order"),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(PREFERENCE_KIND_LENGTH), nullable=False)
    effect: Mapped[str] = mapped_column(String(PREFERENCE_EFFECT_LENGTH), nullable=False)
    value: Mapped[str | None] = mapped_column(String(PREFERENCE_VALUE_LENGTH))
    minimum: Mapped[int | None] = mapped_column(Integer)
    maximum: Mapped[int | None] = mapped_column(Integer)


class ProviderProfileMappingRecord(Base):
    """ORM representation of an external-to-internal profile mapping."""

    __tablename__ = "provider_profile_mappings"
    __table_args__ = (
        UniqueConstraint("provider", "external_profile_id", name="uq_provider_profile_mappings_external"),
    )

    id: Mapped[str] = mapped_column(String(MEDIA_ID_LENGTH), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(MEDIA_ID_LENGTH), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH), nullable=False)
    external_profile_id: Mapped[str] = mapped_column(String(EXTERNAL_PROFILE_ID_LENGTH), nullable=False)
    synchronized_at: Mapped[str | None] = mapped_column(String(TIMESTAMP_LENGTH))
