"""Numeric sentinels for missing values (spec E18). The graph has no null for numbers; the loader wrote these.
data/staged/full/manifest.json is the source of record; tests assert this table equals it."""
SENTINELS = {
    "dist1": -1.0, "dist2": -1.0, "d1": -1.0, "d2": -1.0, "d3": -1.0, "d4": -123.0, "d5": -1.0, "d10": -1.0, "d15": -84.0,
    "v51": -1.0, "v52": -1.0, "v79": -1.0, "v93": -1.0, "v94": -1.0, "v217": -1.0, "v258": -1.0, "v264": -1.0, "v308": -1.0,
}
INT_MISSING = {"addr1": -1, "addr2": -1}
CARD_MISSING = {"card2": -1.0, "card3": -1.0, "card5": -1.0}


def clean_value(name, value):
    """Sentinel -> None; empty string -> None; everything else unchanged."""
    if value is None or value == "":
        return None
    if name in SENTINELS and float(value) == SENTINELS[name]:
        return None
    if name in INT_MISSING and int(value) == INT_MISSING[name]:
        return None
    if name in CARD_MISSING and float(value) == CARD_MISSING[name]:
        return None
    return value


def clean_record(rec, names=None):
    """Return a copy with sentinels/empties mapped to None (only for the named fields when given)."""
    return {k: (clean_value(k, v) if names is None or k in names else v) for k, v in rec.items()}
