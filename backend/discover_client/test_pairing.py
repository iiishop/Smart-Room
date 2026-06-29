from discover_client.pairing import (
    PAIRING_RULES,
    build_pairing_prompt,
    normalize_visual_profile,
    pairing_response_schema,
    score_candidates,
    shortlist_network_profiles,
)


def test_pairing_score_is_computed_from_rule_verdicts() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "temperature_humidity_sensor",
            "vendor_candidates": ["Govee"],
            "model_candidates": ["H5179"],
            "visible_text": ["Govee", "H5179"],
            "capabilities": ["temperature", "humidity"],
            "physical_features": ["LCD screen"],
        }
    )
    network = [
        {
            "canonical_device_id": "urn:smartroom:device:1",
            "display_name": "Govee H5179 temperature and humidity sensor",
            "summary": "Govee H5179",
            "vendor": "Govee",
            "model_candidates": ["H5179"],
            "device_type": "temperature_humidity_sensor",
            "capabilities": ["temperature", "humidity"],
            "identifiers": {"mqtt_topic_prefix": ["govee/H5179/abc"]},
            "connections": {"ip": ["192.168.1.10"], "mac": []},
            "last_seen": 10.0,
        }
    ]
    llm_payload = {
        "candidates": [
            {
                "candidate_id": "urn:smartroom:device:1",
                "display_name_zh": "Govee H5179 温湿度传感器",
                "summary_zh": "通过 MQTT 上报温度和湿度。",
                "score": 1,
                "confidence": 0.01,
                "rules": [
                    {"rule_id": "exact_model_match", "verdict": "match"},
                    {"rule_id": "model_family_match", "verdict": "match"},
                    {"rule_id": "vendor_match", "verdict": "match"},
                    {"rule_id": "device_type_match", "verdict": "match"},
                    {"rule_id": "core_capability_match", "verdict": "match"},
                    {"rule_id": "secondary_capability_match", "verdict": "unknown"},
                    {"rule_id": "visible_identifier_support", "verdict": "match"},
                    {"rule_id": "physical_function_consistency", "verdict": "match"},
                ],
            }
        ]
    }

    result = score_candidates(visual, network, llm_payload)[0]

    assert result["score"] == 95
    assert result["score"] != llm_payload["candidates"][0]["score"]
    assert result["evidence_coverage_percent"] == 95
    assert result["display_name"] == "Govee H5179 温湿度传感器"


def test_pairing_conflict_penalty_changes_ranking() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "camera",
            "vendor_candidates": ["Acme"],
            "model_candidates": ["CAM100"],
            "capabilities": ["camera"],
        }
    )
    candidates = [
        {
            "canonical_device_id": "correct",
            "display_name": "Acme CAM100 camera",
            "vendor": "Acme",
            "model_candidates": ["CAM100"],
            "device_type": "camera",
            "capabilities": ["camera"],
            "identifiers": {},
            "connections": {},
        },
        {
            "canonical_device_id": "wrong",
            "display_name": "Other plug",
            "vendor": "Other",
            "model_candidates": ["PLUG9"],
            "device_type": "smart_switch",
            "capabilities": ["power"],
            "identifiers": {},
            "connections": {},
        },
    ]

    results = score_candidates(visual, candidates)

    assert results[0]["canonical_device_id"] == "correct"
    assert results[0]["score"] > results[1]["score"]


def test_pairing_prompt_and_schema_keep_response_compact() -> None:
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

    assert "partial evidence rather than proof" in prompt
    assert "visual_evidence" not in prompt
    assert "network_evidence" not in prompt
    assert '"reason"' not in prompt
    verdicts_schema = (
        schema["json_schema"]["schema"]["properties"]["candidates"]["items"]["properties"]["verdicts"]
    )
    assert verdicts_schema["minItems"] == len(PAIRING_RULES)
    assert verdicts_schema["maxItems"] == len(PAIRING_RULES)


def test_missing_mqtt_capability_is_unknown_not_conflict() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "lamp",
            "capabilities": ["light", "temperature"],
        }
    )
    network = [
        {
            "canonical_device_id": "partial-node",
            "display_name": "Homemade MQTT node",
            "device_type": "temperature_sensor",
            "capabilities": ["temperature"],
            "identifiers": {},
            "connections": {},
        }
    ]

    result = score_candidates(visual, network)[0]
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert rules["device_type_match"]["verdict"] == "unknown"
    assert rules["core_capability_match"]["verdict"] == "match"


def test_named_power_endpoint_matches_visual_device_without_identity_conflicts() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "3d_printer",
            "vendor_candidates": ["Prusa Research"],
            "model_candidates": ["Original Prusa i3 MK3S"],
            "visible_text": ["ORIGINAL PRUSA", "PRUSA"],
            "capabilities": ["3d_printing", "filament_extrusion"],
        }
    )
    endpoint = {
        "canonical_device_id": "prusa-power",
        "display_name": "Gosund UP111 Prusa2 Tasmota",
        "summary": "Energy and power endpoint",
        "vendor": "Tasmota",
        "model_candidates": ["Gosund UP111"],
        "device_type": "energy_monitor",
        "capabilities": ["energy", "power"],
        "identifiers": {
            "mqtt_topic_prefix": ["site/energy/gosund/pertuina-the-prusa-2"],
        },
        "data": {"power": {"value": "ON"}},
        "operations": [{"topic": "site/energy/gosund/pertuina-the-prusa-2/POWER"}],
    }
    all_conflicts = {
        "candidates": [
            {
                "candidate_id": "prusa-power",
                "verdicts": ["conflict"] * len(PAIRING_RULES),
            }
        ]
    }

    baseline = score_candidates(visual, [endpoint])[0]
    result = score_candidates(visual, [endpoint], all_conflicts)[0]
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert baseline["score"] >= 45
    assert result["score"] >= 45
    assert rules["model_family_match"]["verdict"] == "match"
    assert rules["visible_identifier_support"]["verdict"] == "match"
    assert rules["physical_function_consistency"]["verdict"] == "match"
    assert rules["vendor_match"]["verdict"] == "unknown"
    assert rules["device_type_match"]["verdict"] == "unknown"


def test_shortlist_recalls_named_endpoint_after_many_recent_distractors() -> None:
    visual = normalize_visual_profile(
        {
            "device_type": "3d_printer",
            "vendor_candidates": ["Prusa Research"],
            "model_candidates": ["Original Prusa i3 MK3S"],
            "visible_text": ["PRUSA"],
            "capabilities": ["3d_printing"],
        }
    )
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

    shortlist = shortlist_network_profiles(visual, distractors + [expected], 10)

    assert shortlist[0]["canonical_device_id"] == "expected"
