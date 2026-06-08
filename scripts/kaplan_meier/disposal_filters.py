from __future__ import annotations

from dataclasses import dataclass


BLANK_LABEL = "[blank]"
TRANSFERRED_MADE_OVER_BUCKET = "transferred / made over"


@dataclass(frozen=True, slots=True)
class DisposalTag:
    raw: str
    primary: str
    secondary_raw: str
    secondary_group: str


def tag_disposal(raw_value: str | None) -> DisposalTag:
    raw = (raw_value or "").strip()
    if not raw:
        return DisposalTag(
            raw=BLANK_LABEL,
            primary="blank",
            secondary_raw=BLANK_LABEL,
            secondary_group="blank",
        )

    if "--" in raw:
        primary_raw, secondary_raw = raw.split("--", 1)
        primary = primary_raw.strip() or "unprefixed"
        secondary = secondary_raw.strip() or BLANK_LABEL
    else:
        primary = "unprefixed"
        secondary = raw

    return DisposalTag(
        raw=raw,
        primary=primary,
        secondary_raw=secondary,
        secondary_group=_normalize_secondary_group(secondary),
    )


def is_excluded_disposal(raw_value: str | None) -> bool:
    return tag_disposal(raw_value).secondary_group == TRANSFERRED_MADE_OVER_BUCKET


def _normalize_secondary_group(value: str) -> str:
    text = (value or "").strip()
    if not text or text == BLANK_LABEL:
        return "blank"

    normalized = text.upper().replace("\\", "").replace(".", "")
    normalized = " ".join(normalized.split())

    if "AQUITTED" in normalized or "ACQUITTED" in normalized:
        return "acquitted"
    if "CONVICTED" in normalized:
        return "convicted"
    if "DISMISSED" in normalized:
        return "dismissed"
    if "WITHDRAW" in normalized or "WITH DRAWN" in normalized:
        return "withdrawn"
    if "TRANSFER" in normalized or "MADE OVER" in normalized:
        return TRANSFERRED_MADE_OVER_BUCKET
    if "ABATED" in normalized:
        return "abated"
    if "ALLOWED" in normalized:
        return "allowed"
    if "CLOSED" in normalized or normalized == "DISPOSED":
        return "closed / disposed"
    if "COMPOUND" in normalized or "COMPROMIS" in normalized:
        return "compounded / compromised"
    if "DISCHARGED" in normalized:
        return "discharged"
    if "FINED" in normalized:
        return "fined"
    if "PROCEEDINGS STOPPED" in normalized:
        return "proceedings stopped"
    return "other"
