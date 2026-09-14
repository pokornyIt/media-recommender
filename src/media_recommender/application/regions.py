"""Deterministic broad-region classification for ISO country codes."""

# ruff: noqa: SIM905 - space-delimited tables are easier to audit than hundreds of quoted entries.

from __future__ import annotations

from enum import StrEnum


class ProductionRegion(StrEnum):
    """Broad production region used by structured recommendation criteria."""

    AFRICA = "africa"
    ASIA = "asia"
    EUROPE = "europe"
    NORTH_AMERICA = "north_america"
    SOUTH_AMERICA = "south_america"
    OCEANIA = "oceania"


_AFRICA = frozenset(
    "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA "
    "MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG EH ZM ZW".split()
)
_ASIA = frozenset(
    "AF AM AZ BH BD BT BN KH CN CY GE HK IN ID IR IQ IL JP JO KZ KP KR KW KG LA LB MO MY MV MN MM NP OM PK "
    "PS PH QA SA SG LK SY TW TJ TH TL TR TM AE UZ VN YE".split()
)
_EUROPE = frozenset(
    "AL AD AT BY BE BA BG HR CZ DK EE FI FR DE GR VA HU IS IE IT XK LV LI LT LU MT MD MC ME NL MK NO PL PT "
    "RO RU SM RS SK SI ES SE CH UA GB".split()
)
_NORTH_AMERICA = frozenset(
    "AI AG AW BS BB BZ BM BQ CA KY CR CU CW DM DO SV GL GD GP GT HT HN JM MQ MX MS NI PA PR BL KN LC MF "
    "PM VC SX TT TC US VG VI".split()
)
_SOUTH_AMERICA = frozenset("AR BO BR CL CO EC FK GF GY PY PE SR UY VE".split())
_OCEANIA = frozenset("AS AU CK FJ PF GU KI MH FM NR NC NZ NU NF MP PW PG PN WS SB TK TO TV UM VU WF".split())

COUNTRY_REGIONS = {
    **dict.fromkeys(_AFRICA, ProductionRegion.AFRICA),
    **dict.fromkeys(_ASIA, ProductionRegion.ASIA),
    **dict.fromkeys(_EUROPE, ProductionRegion.EUROPE),
    **dict.fromkeys(_NORTH_AMERICA, ProductionRegion.NORTH_AMERICA),
    **dict.fromkeys(_SOUTH_AMERICA, ProductionRegion.SOUTH_AMERICA),
    **dict.fromkeys(_OCEANIA, ProductionRegion.OCEANIA),
}


def production_region(country_code: str) -> ProductionRegion | None:
    """Return the configured broad region for an ISO country code.

    :param country_code: ISO 3166-1 alpha-2 country code.
    :return: Broad region, or ``None`` when the code is not classified.
    """
    return COUNTRY_REGIONS.get(country_code.strip().upper())
