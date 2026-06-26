from discover_client.pairing import normalize_visual_profile, score_candidates


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
