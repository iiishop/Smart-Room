from discover_client.pairing import (
    PAIRING_RULES,
    build_pairing_prompt,
    normalize_visual_profile,
    pairing_response_schema,
    score_candidates,
    shortlist_network_profiles,
)


def _explicit_classification(source: str = "Home Assistant MQTT discovery") -> dict:
    return {
        "method": "explicit MQTT discovery metadata",
        "metadata_sources": [source],
        "confidence": 0.95,
    }


def _prusa_visual() -> dict:
    return normalize_visual_profile(
        {
            "summary": "An enclosed Prusa 3D printer.",
            "device_type": "3d_printer",
            "vendor_candidates": ["Prusa Research"],
            "model_candidates": ["Original Prusa i3 MK3S", "Original Prusa i3 MK3"],
            "visible_text": ["ORIGINAL PRUSA", "PRUSA"],
            "capabilities": ["3d_printing", "filament_extrusion"],
        }
    )


def test_structured_exact_match_becomes_same_physical_device() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "temperature_humidity_sensor",
            "vendor_candidates": ["Govee"],
            "model_candidates": ["H5179"],
            "visible_text": ["Govee", "H5179"],
            "capabilities": ["temperature", "humidity"],
        }
    )
    network = [
        {
            "canonical_device_id": "govee-1",
            "display_name": "Bedroom sensor",
            "vendor": "Govee",
            "model_candidates": ["H5179"],
            "device_type": "temperature_humidity_sensor",
            "capabilities": ["temperature", "humidity"],
            "classification": _explicit_classification(),
            "identifiers": {"metadata_identifier": ["AA11BB22CC33"]},
            "connections": {},
        }
    ]

    result = score_candidates(visual, network)[0]

    assert result["relation"] == "same_physical_device"
    assert result["identity_confidence_percent"] >= 60
    assert result["score"] == result["confidence_percent"]
    assert result["retrieval_relevance"] > 0


def test_stable_visible_identifier_is_strong_identity_evidence() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "sensor",
            "visible_text": ["Serial SN123456"],
        }
    )
    network = [
        {
            "canonical_device_id": "serial-match",
            "display_name": "Anonymous MQTT sensor",
            "identifiers": {"metadata_identifier": ["SN123456"]},
            "connections": {},
        }
    ]

    result = score_candidates(visual, network)[0]

    assert result["relation"] == "same_physical_device"
    assert result["identity_confidence_percent"] >= 60


def test_pairing_prompt_and_schema_request_relation_and_compact_verdicts() -> None:
    visual = normalize_visual_profile(
        {
            "summary": "A homemade lamp with a temperature sensor.",
            "device_type": "lamp",
            "capabilities": ["light", "temperature"],
        }
    )
    network = [
        {
            "canonical_device_id": "candidate-1",
            "display_name": "MQTT node",
            "data": {"temperature": {"value": 21.0}},
            "operations": [{"name": "set brightness", "topic": "lamp/set"}],
        }
    ]

    prompt = build_pairing_prompt(visual, network)
    schema = pairing_response_schema()

    assert "cannot prove physical identity" in prompt
    assert "smart plug named after a printer" in prompt
    assert '"relation":"unknown"' in prompt
    candidate_schema = schema["json_schema"]["schema"]["properties"]["candidates"]["items"]
    assert "relation" in candidate_schema["required"]
    verdicts_schema = candidate_schema["properties"]["verdicts"]
    assert verdicts_schema["minItems"] == len(PAIRING_RULES)
    assert verdicts_schema["maxItems"] == len(PAIRING_RULES)


def test_weak_keyword_capability_does_not_create_a_match() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "lamp",
            "capabilities": ["light"],
        }
    )
    lightning = {
        "canonical_device_id": "lightning-distance",
        "display_name": "Blitzortung Lightning Distance network device",
        "device_type": "network_device",
        "capabilities": ["light"],
        "classification": {
            "method": "structural MQTT schema plus semantic fallback",
            "metadata_sources": [],
            "confidence": 0.6,
        },
        "identifiers": {
            "mqtt_topic_prefix": [
                "homeassistant/sensor/blitzortung_lightning_distance"
            ]
        },
        "connections": {},
    }

    result = score_candidates(visual, [lightning])[0]

    assert result["relation"] == "unknown"
    assert result["confidence_percent"] == 0
    rules = {rule["rule_id"]: rule for rule in result["rules"]}
    assert rules["capability_consistency"]["verdict"] == "unknown"


def test_named_prusa_smart_plug_is_power_relation_not_same_device() -> None:
    endpoint = {
        "canonical_device_id": "prusa-power",
        "display_name": "Gosund UP111 Prusa2 Tasmota",
        "summary": "Gosund UP111 Prusa2 Tasmota; capabilities: energy, power",
        "vendor": "Tasmota",
        "model_candidates": ["Gosund UP111"],
        "device_type": "energy_monitor",
        "capabilities": ["energy", "power"],
        "classification": _explicit_classification("Tasmota discovery metadata"),
        "identifiers": {
            "mqtt_topic_prefix": [
                "UCL/OPS/107/EM/gosund/pertuina-the-prusa-2"
            ],
            "metadata_identifier": ["C4DD572ACF3E"],
        },
        "connections": {},
        "data": {"power": {"value": "ON"}},
        "operations": [
            {
                "topic": "UCL/OPS/107/EM/gosund/pertuina-the-prusa-2/POWER",
                "accepted_values": ["ON", "OFF", "TOGGLE"],
            }
        ],
    }

    result = score_candidates(_prusa_visual(), [endpoint])[0]
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert result["relation"] == "powers"
    assert result["identity_confidence_percent"] == 0
    assert result["relationship_confidence_percent"] > 0
    assert result["confidence_percent"] < 60
    assert rules["structured_model_consistency"]["verdict"] == "conflict"
    assert rules["mutable_label_support"]["verdict"] == "match"
    assert rules["endpoint_role_consistency"]["verdict"] == "match"


def test_printer_telemetry_can_be_reviewed_as_direct_device_endpoint() -> None:
    telemetry = {
        "canonical_device_id": "coreone-2",
        "display_name": "Student Ucfnlwa Prusa Coreone 2 environment sensor",
        "device_type": "environment_sensor",
        "capabilities": ["temperature"],
        "classification": {
            "method": "structural MQTT schema plus semantic fallback",
            "metadata_sources": [],
            "confidence": 0.6,
        },
        "identifiers": {
            "mqtt_topic_prefix": ["student/ucfnlwa/prusa/CoreOne-2"]
        },
        "connections": {},
        "data": {
            "state": {},
            "temp_nozzle": {},
            "target_nozzle": {},
            "temp_bed": {},
            "target_bed": {},
            "axis_z": {},
            "speed": {},
            "flow": {},
            "fan_hotend_rpm": {},
        },
        "operations": [],
    }
    llm_payload = {
        "candidates": [
            {
                "candidate_id": "coreone-2",
                "relation": "same_physical_device",
                "verdicts": [
                    "unknown",
                    "unknown",
                    "unknown",
                    "match",
                    "match",
                    "match",
                    "match",
                    "match",
                ],
            }
        ]
    }

    result = score_candidates(_prusa_visual(), [telemetry], llm_payload)[0]

    assert result["relation"] == "same_physical_device"
    assert result["identity_confidence_percent"] >= 45
    assert "Telemetry / operations" in result["evidence_summary"]


def test_retrieval_relevance_is_not_added_to_identity_confidence() -> None:
    candidate = {
        "canonical_device_id": "named-only",
        "display_name": "PRUSA workshop label",
        "identifiers": {
            "mqtt_topic_prefix": ["arbitrary/user/chosen/prusa"]
        },
        "connections": {},
    }

    result = score_candidates(_prusa_visual(), [candidate])[0]

    assert result["retrieval_relevance"] > result["confidence_percent"]
    assert result["identity_confidence_percent"] <= 4
    assert result["confidence_level"] == "insufficient"


def test_shortlist_recalls_named_endpoint_without_promoting_it_to_identity() -> None:
    distractors = [
        {
            "canonical_device_id": f"distractor-{index}",
            "display_name": f"Room {index} environment sensor",
            "vendor": "Building",
            "model_candidates": [f"ENV{index}"],
            "device_type": "environment_sensor",
            "capabilities": ["temperature"],
            "identifiers": {"mqtt_topic_prefix": [f"building/room/{index}"]},
            "last_seen": 10_000.0 + index,
        }
        for index in range(80)
    ]
    expected = {
        "canonical_device_id": "expected",
        "display_name": "Gosund UP111 Prusa2 Tasmota",
        "vendor": "Tasmota",
        "model_candidates": ["Gosund UP111"],
        "device_type": "energy_monitor",
        "capabilities": ["energy", "power"],
        "identifiers": {"mqtt_topic_prefix": ["site/gosund/prusa-2"]},
        "last_seen": 1.0,
    }

    shortlist = shortlist_network_profiles(_prusa_visual(), distractors + [expected], 10)
    scored = score_candidates(_prusa_visual(), [expected])[0]

    assert shortlist[0]["canonical_device_id"] == "expected"
    assert scored["relation"] == "powers"
    assert scored["identity_confidence_percent"] <= 4
