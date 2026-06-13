"""Property key normalizer — canonical ↔ dialect translations."""

DIALECT_MAPPINGS: dict[str, dict[str, str]] = {
    "tasmota": {"POWER": "power", "DIMMER": "brightness", "STATE": "power", "COLOR": "color"},
    "zigbee2mqtt": {
        "state": "power",
        "brightness": "brightness",
        "color_temp": "color_temperature",
    },
    "subtopic": {},
    "flatdict": {},
    "barevalue": {},
}


def to_canonical(dialect: str, key: str) -> str:
    """Map a dialect-specific property key to the canonical name.

    Lowercases the key, then looks it up in DIALECT_MAPPINGS[dialect].
    Falls back to the lowercased key if no mapping exists.
    """
    lower = key.lower()
    mapping = DIALECT_MAPPINGS.get(dialect, {})
    return mapping.get(key, mapping.get(lower, lower))


def to_dialect(dialect: str, canonical_key: str) -> str:
    """Reverse-map a canonical key back to the dialect-specific name.

    Searches DIALECT_MAPPINGS[dialect] for a value matching canonical_key.
    Falls back to the canonical key itself if no reverse mapping exists.
    """
    mapping = DIALECT_MAPPINGS.get(dialect, {})
    for dialect_key, canonical_val in mapping.items():
        if canonical_val == canonical_key:
            return dialect_key
    return canonical_key
