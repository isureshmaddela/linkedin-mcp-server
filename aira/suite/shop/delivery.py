"""Where the shop delivers and what delivery costs (delivery zones), and the tracking link of a shipment.

A zone (``sh_delivery_zones``) has a name, the addresses it covers and a rate:

* covers: countries (codes or names: "IN", "India"), states or regions ("Telangana", "36", "California") and
  postcode or PIN prefixes ("500", "SW1") or numeric ranges ("500001-500099"). An empty list matches anything,
  so a zone of only PIN prefixes covers those PINs in any country.
* rate: ``flat`` (one fee), ``weight`` (bands by the order's weight: up to 500 g costs this, up to 2 kg that;
  heavier than the last band is not delivered there) or ``free_over`` (the fee, free from an amount).

The most specific zone that covers the address wins (postcodes, then regions, then countries, then the order in
the list). Everywhere else is the default zone: the shop's own delivery fee and free-delivery threshold (the one
flat fee the shop had before zones), unless the shop turned "deliver everywhere else" off, when checkout says it
does not deliver there.

Tracking: a shipment's carrier and tracking number make its link (:data:`CARRIERS`, or the shop's own page with
``{number}`` in it); a typed link still works.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from aira.suite import kit
from aira.suite.shop import store
from aira.suite.shop.store import ZONE_RATES

MAX_ZONES = 100
MAX_RULES = 200  # countries, regions or postcodes in one zone
MAX_BANDS = 20

# Carriers whose tracking page takes the number in the address. "custom" is the shop's own page (settings).
CARRIERS: dict[str, tuple[str, str]] = {
    "india_post": (
        "India Post",
        "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx?consignment={number}",
    ),
    "delhivery": ("Delhivery", "https://www.delhivery.com/track/package/{number}"),
    "bluedart": (
        "Blue Dart",
        "https://www.bluedart.com/web/guest/trackdartresult?trackFor=0&trackNo={number}",
    ),
    "dtdc": (
        "DTDC",
        "https://www.dtdc.in/tracking/tracking_results.asp?Ttype=awb_no&strCnno={number}",
    ),
    "custom": ("Other carrier", ""),
}
_TRACKING_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{2,59}$")


# ---------------------------------------------------------------------------
# zones
# ---------------------------------------------------------------------------


def _list(value: Any, label: str) -> list[str]:
    """Rules typed as a list or as text separated by commas or new lines."""
    if isinstance(value, str):
        items = re.split(r"[,\n;]", value)
    elif isinstance(value, list):
        items = [str(item) for item in value]
    elif value in (None, ""):
        items = []
    else:
        raise ValueError(f"Send the {label} as a list.")
    out: list[str] = []
    for item in items:
        cleaned = " ".join(str(item).split())
        if cleaned and cleaned not in out:
            if len(cleaned) > 60:
                raise ValueError(f"One of the {label} is too long.")
            out.append(cleaned)
    if len(out) > MAX_RULES:
        raise ValueError(f"A zone can list at most {MAX_RULES} {label}.")
    return out


_RANGE = re.compile(r"^(\d{2,10})\s*-\s*(\d{2,10})$")


def _postcodes(value: Any) -> list[str]:
    out = []
    for item in _list(value, "postcodes"):
        match = _RANGE.match(item)
        if match:
            low, high = match.groups()
            if len(low) != len(high) or int(low) > int(high):
                raise ValueError(
                    f"{item}: a range is two codes of the same length, the lower first."
                )
            out.append(f"{low}-{high}")
            continue
        key = store.postcode_key(item)
        if not key:
            raise ValueError(f"{item} is not a postcode or PIN prefix.")
        out.append(key)
    return out


def _bands(value: Any, label: str) -> list[dict[str, int]]:
    """Weight bands ``[{"up_to_grams", "cents"}]`` (or text lines "500 | 40.00"), lightest first."""
    rows: list[Any]
    if isinstance(value, str):
        rows = [line for line in value.splitlines() if line.strip()]
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    bands: list[dict[str, int]] = []
    for row in rows[: MAX_BANDS + 1]:
        if isinstance(row, Mapping):
            grams, cents = row.get("up_to_grams"), row.get("cents")
        else:
            parts = [part.strip() for part in str(row).split("|")]
            if len(parts) != 2:
                raise ValueError("Write each weight band as: up to grams | price.")
            grams, cents = parts
        bands.append(
            {
                "up_to_grams": kit.integer(
                    grams,
                    "the band's weight (grams)",
                    minimum=1,
                    maximum=1_000_000,
                ),
                "cents": store.amount(cents, f"the price of a {label} band"),
            }
        )
    if len(bands) > MAX_BANDS:
        raise ValueError(f"A zone can have at most {MAX_BANDS} weight bands.")
    bands.sort(key=lambda band: band["up_to_grams"])
    if len({band["up_to_grams"] for band in bands}) != len(bands):
        raise ValueError("Two weight bands have the same weight.")
    return bands


def zone_values(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**dict(current or {}), **raw}
    rate = kit.choice(merged.get("rate_type"), "the rate", ZONE_RATES, default="flat")
    values = {
        "name": kit.text(
            merged.get("name"), "the zone name", max_len=80, required=True
        ),
        "countries": _list(merged.get("countries"), "countries"),
        "regions": _list(merged.get("regions"), "states or regions"),
        "postcodes": _postcodes(merged.get("postcodes")),
        "rate_type": rate,
        "fee_cents": store.amount(merged.get("fee_cents"), "the delivery fee"),
        "free_over_cents": store.amount(
            merged.get("free_over_cents"), "the free-delivery amount"
        ),
        "bands": _bands(merged.get("bands"), "weight"),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
        "position": kit.integer(
            merged.get("position"), "the position", default=0, minimum=0, maximum=10000
        ),
    }
    if not (values["countries"] or values["regions"] or values["postcodes"]):
        raise ValueError(
            "Say where the zone is: countries, states or regions, or postcode (PIN) prefixes."
        )
    if rate == "weight" and not values["bands"]:
        raise ValueError("Add at least one weight band, like: 1000 | 60.00.")
    if rate == "free_over" and not values["free_over_cents"]:
        raise ValueError("Enter the order amount delivery is free from.")
    # each rate keeps only what it uses
    if rate != "weight":
        values["bands"] = []
    if rate != "free_over":
        values["free_over_cents"] = 0
    if rate == "weight":
        values["fee_cents"] = 0
    return values


def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
    zone = dict(row)
    for key in ("countries", "regions", "postcodes", "bands"):
        zone[key] = kit.loads(zone.get(key), []) or []
    zone["active"] = bool(zone.get("active"))
    return zone


def list_zones(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    return [
        _decode(row)
        for row in kit.fetch_all(
            connection,
            f"SELECT * FROM sh_delivery_zones {where} ORDER BY position, name",  # noqa: S608
        )
    ]


def get_zone(connection: sqlite3.Connection, zone_id: str) -> dict[str, Any]:
    return _decode(kit.require_row(connection, "sh_delivery_zones", zone_id, "zone"))


def _stored(values: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(values)
    for key in ("countries", "regions", "postcodes", "bands"):
        row[key] = kit.dumps(row[key])
    return row


def create_zone(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    if kit.count(connection, "SELECT COUNT(*) FROM sh_delivery_zones") >= MAX_ZONES:
        raise ValueError(f"A shop can have at most {MAX_ZONES} delivery zones.")
    row = _stored(zone_values(raw))
    stamp = kit.now()
    row.update({"id": kit.new_id(), "created_at": stamp, "updated_at": stamp})
    columns = ", ".join(row)
    marks = ", ".join(f":{key}" for key in row)
    connection.execute(
        f"INSERT INTO sh_delivery_zones ({columns}) VALUES ({marks})", row
    )  # noqa: S608 - our keys
    connection.commit()
    return get_zone(connection, row["id"])


def update_zone(
    connection: sqlite3.Connection, zone_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = get_zone(connection, zone_id)
    row = _stored(zone_values(raw, current))
    row["updated_at"] = kit.now()
    kit.update_row(connection, "sh_delivery_zones", zone_id, row)
    connection.commit()
    return get_zone(connection, zone_id)


def delete_zone(connection: sqlite3.Connection, zone_id: str) -> None:
    kit.require_row(connection, "sh_delivery_zones", zone_id, "zone")
    connection.execute("DELETE FROM sh_delivery_zones WHERE id = ?", (zone_id,))
    connection.commit()


def _postcode_matches(rule: str, postcode: str) -> bool:
    if "-" in rule:
        low, high = rule.split("-", 1)
        head = postcode[: len(low)]
        return (
            head.isdigit()
            and len(head) == len(low)
            and int(low) <= int(head) <= int(high)
        )
    return postcode.startswith(rule)


def covers(zone: Mapping[str, Any], address: Mapping[str, Any]) -> bool:
    """Does the zone cover this address? Every kind of rule the zone has must match (and needs the address part)."""
    if zone["countries"]:
        country = store.country_key(address.get("country"))
        if not country or country not in {
            store.country_key(c) for c in zone["countries"]
        }:
            return False
    if zone["regions"]:
        region = store.region_key(address.get("region"))
        if not region or region not in {store.region_key(r) for r in zone["regions"]}:
            return False
    if zone["postcodes"]:
        postcode = store.postcode_key(address.get("postcode"))
        if not postcode or not any(
            _postcode_matches(rule, postcode) for rule in zone["postcodes"]
        ):
            return False
    return True


def _specificity(zone: Mapping[str, Any]) -> tuple[int, int, int, int]:
    return (
        0 if zone["postcodes"] else 1,
        0 if zone["regions"] else 1,
        0 if zone["countries"] else 1,
        int(zone.get("position") or 0),
    )


def default_zone(shop: Mapping[str, Any]) -> dict[str, Any]:
    """Everywhere else, at the shop's own fee (what the shop charged before zones)."""
    free_over = int(shop.get("free_delivery_over_cents") or 0)
    return {
        "id": "",
        "name": "Everywhere else",
        "countries": [],
        "regions": [],
        "postcodes": [],
        "rate_type": "free_over" if free_over else "flat",
        "fee_cents": int(shop.get("delivery_fee_cents") or 0),
        "free_over_cents": free_over,
        "bands": [],
        "active": bool(shop.get("deliver_elsewhere", True)),
        "default": True,
    }


def choose_zone(
    connection: sqlite3.Connection, shop: Mapping[str, Any], address: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The zone that prices delivery to this address, or None when the shop does not deliver there."""
    zones = sorted(list_zones(connection, active_only=True), key=_specificity)
    for zone in zones:
        if covers(zone, address):
            return dict(zone)
    fallback = default_zone(shop)
    return fallback if fallback["active"] else None


def rate(
    zone: Mapping[str, Any], goods_cents: int, weight_grams: int, currency: str
) -> tuple[int, str]:
    """``(fee, problem)``: what delivery costs in the zone, or why it cannot be delivered there."""
    kind = zone["rate_type"]
    if kind == "weight":
        for band in zone["bands"]:
            if weight_grams <= int(band["up_to_grams"]):
                return int(band["cents"]), ""
        heaviest = int(zone["bands"][-1]["up_to_grams"]) if zone["bands"] else 0
        return 0, (
            f"This order weighs {weight_label(weight_grams)}, more than the shop delivers to "
            f"{zone['name']} ({weight_label(heaviest)} at most). Choose pickup, or order less."
        )
    fee = int(zone.get("fee_cents") or 0)
    free_over = int(zone.get("free_over_cents") or 0)
    if free_over and goods_cents >= free_over:
        return 0, ""
    return fee, ""


def weight_label(grams: int) -> str:
    if grams >= 1000:
        kilos = grams / 1000
        return f"{kilos:.1f}".rstrip("0").rstrip(".") + " kg"
    return f"{grams} g"


def zones_in_use(connection: sqlite3.Connection) -> bool:
    return bool(
        kit.count(connection, "SELECT COUNT(*) FROM sh_delivery_zones WHERE active = 1")
    )


def address_needs(connection: sqlite3.Connection) -> dict[str, bool]:
    """Which address parts checkout asks for, so a zone can be chosen (none when the shop has no zones)."""
    zones = list_zones(connection, active_only=True)
    return {
        "country": any(zone["countries"] for zone in zones),
        "region": any(zone["regions"] for zone in zones),
        "postcode": any(zone["postcodes"] for zone in zones),
    }


def public_zones(
    connection: sqlite3.Connection, shop: Mapping[str, Any]
) -> dict[str, Any]:
    """What the storefront says about delivery before an address is typed."""
    zones = list_zones(connection, active_only=True)
    fallback = default_zone(shop)
    return {
        "zones": [
            {
                "name": zone["name"],
                "rate_type": zone["rate_type"],
                "fee_cents": zone["fee_cents"],
                "free_over_cents": zone["free_over_cents"],
                "bands": zone["bands"],
            }
            for zone in zones
        ],
        "elsewhere": fallback["active"],
        "asks": address_needs(connection),
        "default_country": shop.get("default_country") or "",
    }


def describe(zone: Mapping[str, Any], currency: str) -> str:
    """A zone's rate in words, for the shop's list."""
    if zone["rate_type"] == "weight":
        return ", ".join(
            f"up to {weight_label(int(band['up_to_grams']))} {kit.fmt_money(int(band['cents']), currency)}"
            for band in zone["bands"]
        )
    fee = (
        kit.fmt_money(int(zone["fee_cents"] or 0), currency)
        if zone["fee_cents"]
        else "free"
    )
    if zone["rate_type"] == "free_over" or zone.get("free_over_cents"):
        return f"{fee}, free over {kit.fmt_money(int(zone['free_over_cents'] or 0), currency)}"
    return fee


# ---------------------------------------------------------------------------
# tracking links
# ---------------------------------------------------------------------------


def tracking_number(value: Any) -> str:
    text = kit.text(value, "the tracking number", max_len=60)
    if text and not _TRACKING_NUMBER.match(text):
        raise ValueError(
            "A tracking number is letters and digits (with - . / allowed)."
        )
    return text


def carrier_choice(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in CARRIERS:
        raise ValueError("Choose a carrier from the list, or Other carrier.")
    return text


def tracking_link(shop: Mapping[str, Any], carrier: str, number: str) -> str:
    """The tracking page for a carrier and number ('' when there is none to build)."""
    if not carrier or not number:
        return ""
    template = (
        str(shop.get("custom_tracking_url") or "")
        if carrier == "custom"
        else CARRIERS.get(carrier, ("", ""))[1]
    )
    if not template:
        return ""
    return template.replace("{number}", quote(number.replace(" ", ""), safe=""))


def carrier_name(carrier: str) -> str:
    return CARRIERS.get(carrier, ("", ""))[0] if carrier != "custom" else ""


def carriers(shop: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The carriers the ship form offers (the shop's own page only once it is set)."""
    return [
        {
            "id": key,
            "name": name,
            "builds_link": bool(url)
            or (key == "custom" and bool(shop.get("custom_tracking_url"))),
        }
        for key, (name, url) in CARRIERS.items()
    ]
