"""SQLAlchemy ORM models for normalized shared catalog data."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime.
from typing import Final

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Integer, String, Table, UniqueConstraint
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
