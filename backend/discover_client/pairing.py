"""Provenance-aware matching between visual objects and network endpoints.

Candidate retrieval, relationship inference, and confidence scoring are kept
separate. Mutable MQTT labels can retrieve a candidate, but cannot by
themselves establish physical identity.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any


PAIRING_RELATIONS = {
    "same_physical_device",
    "powers",
    "controls",
    "monitors",
    "gateway_for",
    "related",
    "unrelated",
    "unknown",
}

RELATION_LABELS = {
    "same_physical_device": "Same physical device",
    "powers": "Power endpoint",
    "controls": "Control endpoint",
    "monitors": "Monitoring endpoint",
    "gateway_for": "Gateway",
    "related": "Related endpoint",
    "unrelated": "Unrelated",
    "unknown": "Relationship unknown",
}

# These weights express evidence strength, not trained probabilities.
PAIRING_RULES: list[dict[str, Any]] = [
    {
        "id": "stable_identifier_support",
        "label": "Stable identifier",
        "identity_weight": 60,
        "relation_weight": 10,
        "conflict": -60,
    },
    {
        "id": "structured_model_consistency",
        "label": "Structured model",
        "identity_weight": 30,
        "relation_weight": 0,
        "conflict": -30,
    },
    {
        "id": "structured_vendor_consistency",
        "label": "Structured vendor",
        "identity_weight": 10,
        "relation_weight": 0,
        "conflict": -12,
    },
    {
        "id": "device_class_consistency",
        "label": "Device class",
        "identity_weight": 16,
        "relation_weight": 6,
        "conflict": -18,
    },
    {
        "id": "capability_consistency",
        "label": "Capabilities",
        "identity_weight": 12,
        "relation_weight": 10,
        "conflict": -8,
    },
    {
        "id": "telemetry_consistency",
        "label": "Telemetry / operations",
        "identity_weight": 18,
        "relation_weight": 18,
        "conflict": -15,
    },
    {
        "id": "mutable_label_support",
        "label": "Mutable MQTT label",
        "identity_weight": 4,
        "relation_weight": 10,
        "conflict": -3,
    },
    {
        "id": "endpoint_role_consistency",
        "label": "Endpoint role",
        "identity_weight": 12,
        "relation_weight": 26,
        "conflict": -20,
    },
]

_RULES_BY_ID = {str(rule["id"]): rule for rule in PAIRING_RULES}
_VALID_VERDICTS = {"match", "conflict", "unknown"}
_GENERIC_RETRIEVAL_TERMS = {
    "and",
    "device",
    "original",
    "research",
    "sensor",
    "the",
    "with",
}
_STRONG_IDENTIFIER_KEYS = {
    "bluetooth_address",
    "mac",
    "metadata_identifier",
    "serial",
    "serial_number",
    "ssdp_usn",
    "zigbee_ieee",
}
_POWER_CAPABILITIES = {"current", "energy", "power", "relay", "switch", "voltage"}
_CONTROL_DEVICE_TYPES = {"energymonitor", "smartplug", "smartswitch"}


def parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def normalize_visual_profile(payload: dict[str, Any] | None, fallback_text: str = "") -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    return {
        "summary": str(source.get("summary") or source.get("summary_zh") or fallback_text or "").strip(),
        "device_type": str(source.get("device_type") or "").strip(),
        "vendor_candidates": _string_list(source.get("vendor_candidates")),
        "model_candidates": _string_list(source.get("model_candidates")),
        "visible_text": _string_list(source.get("visible_text")),
        "capabilities": _string_list(source.get("capabilities")),
        "physical_features": _string_list(source.get("physical_features")),
        "uncertainties": _string_list(source.get("uncertainties")),
    }


def build_pairing_prompt(visual_profile: dict[str, Any], network_profiles: list[dict[str, Any]]) -> str:
    compact_candidates = [_compact_prompt_profile(profile) for profile in network_profiles]
    rule_ids = [str(rule["id"]) for rule in PAIRING_RULES]
    relations = sorted(PAIRING_RELATIONS)
    return (
        "Resolve relationships between one visually observed physical object and each network endpoint. "
        "This is evidence extraction, not free-form scoring. MQTT display names, friendly names, topic paths, "
        "and payload strings are mutable user assertions: they may retrieve a candidate and suggest a relation, "
        "but cannot prove physical identity or an exact model. Structured protocol metadata is stronger but is "
        "still self-declared unless backed by a stable identifier. Missing MQTT fields mean unknown, not conflict. "
        "A smart plug named after a printer is normally relation=powers, not same_physical_device. A telemetry "
        "endpoint whose schema is characteristic of the visual device may be same_physical_device even when its "
        "generic network classifier is wrong. Explicit model disagreement is a conflict only when both model "
        "claims are structured or visibly observed; disagreement with a mutable topic label is weak. "
        "Choose one relation and one verdict for every supplied rule. Do not output scores, probabilities, names, "
        "summaries, explanations, or extra keys; deterministic code combines the evidence. Return every candidate.\n\n"
        "VISUAL_PROFILE:\n"
        + json.dumps(visual_profile, ensure_ascii=False, separators=(",", ":"))
        + "\n\nNETWORK_CANDIDATES:\n"
        + json.dumps(compact_candidates, ensure_ascii=False, separators=(",", ":"))
        + "\n\nRELATIONS:\n"
        + json.dumps(relations, separators=(",", ":"))
        + "\n\nRULE_IDS_IN_VERDICT_ORDER:\n"
        + json.dumps(rule_ids, separators=(",", ":"))
        + "\n\nReturn exactly:\n"
        '{"candidates":[{"candidate_id":"id","relation":"unknown","verdicts":'
        '["unknown","unknown","unknown","unknown","unknown","unknown","unknown","unknown"]}]}'
    )


def pairing_response_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "smart_room_pairing_evidence",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidates"],
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["candidate_id", "relation", "verdicts"],
                            "properties": {
                                "candidate_id": {"type": "string"},
                                "relation": {"type": "string", "enum": sorted(PAIRING_RELATIONS)},
                                "verdicts": {
                                    "type": "array",
                                    "minItems": len(PAIRING_RULES),
                                    "maxItems": len(PAIRING_RULES),
                                    "items": {"type": "string", "enum": sorted(_VALID_VERDICTS)},
                                },
                            },
                        },
                    }
                },
            },
        },
    }


def score_candidates(
    visual_profile: dict[str, Any],
    network_profiles: list[dict[str, Any]],
    llm_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score relationship evidence without mixing in retrieval relevance."""
    llm_by_candidate = _llm_results_by_candidate(llm_payload)
    frequencies = _mutable_term_frequencies(network_profiles)
    total_profiles = max(1, len(network_profiles))
    results: list[dict[str, Any]] = []

    for profile in network_profiles:
        candidate_id = str(profile.get("canonical_device_id") or "")
        heuristic = _heuristic_rule_results(
            visual_profile,
            profile,
            frequencies,
            total_profiles,
        )
        llm_result = llm_by_candidate.get(candidate_id, {})
        incoming = llm_result.get("rules", {})
        rules: list[dict[str, Any]] = []

        for rule in PAIRING_RULES:
            rule_id = str(rule["id"])
            decision, source = _merge_rule_decision(
                rule_id,
                heuristic[rule_id],
                incoming.get(rule_id),
                visual_profile,
                profile,
            )
            verdict = str(decision.get("verdict") or "unknown")
            if verdict not in _VALID_VERDICTS:
                verdict = "unknown"
            rules.append(
                {
                    "rule_id": rule_id,
                    "label": str(rule["label"]),
                    "verdict": verdict,
                    "points": _legacy_rule_points(rule, verdict),
                    "max_points": max(
                        int(rule["identity_weight"]),
                        int(rule["relation_weight"]),
                    ),
                    "visual_evidence": str(decision.get("visual_evidence") or ""),
                    "network_evidence": str(decision.get("network_evidence") or ""),
                    "reason": str(decision.get("reason") or ""),
                    "source": source,
                }
            )

        identity_score = _cap_identity_confidence(
            _score_rule_dimension(rules, "identity_weight"),
            rules,
        )
        relation_score = _score_rule_dimension(rules, "relation_weight")
        relation = _select_relation(
            visual_profile,
            profile,
            rules,
            str(llm_result.get("relation") or "unknown"),
            identity_score,
        )
        confidence = _relation_confidence(
            relation,
            identity_score,
            relation_score,
            bool(llm_result),
        )
        coverage = _evidence_coverage(rules)
        retrieval_score = _retrieval_score(
            visual_profile,
            profile,
            frequencies,
            total_profiles,
        )
        support, conflicts = _evidence_summaries(rules)
        level = _confidence_level(confidence)
        display_name = str(profile.get("display_name") or candidate_id)
        results.append(
            {
                "canonical_device_id": candidate_id,
                "display_name": display_name,
                "summary": str(profile.get("summary") or ""),
                "relation": relation,
                "relation_label": RELATION_LABELS[relation],
                "confidence_level": level,
                "identity_confidence_percent": identity_score,
                "relationship_confidence_percent": (
                    identity_score if relation == "same_physical_device" else relation_score
                ),
                "retrieval_relevance": retrieval_score,
                # Compatibility fields consumed by existing Viewer and Quest builds.
                "score": confidence,
                "confidence_percent": confidence,
                "evidence_coverage_percent": coverage,
                "evidence_summary": support,
                "conflict_summary": conflicts,
                "rules": rules,
                "profile": profile,
            }
        )

    results.sort(
        key=lambda item: (
            int(item["confidence_percent"]),
            1 if item["relation"] not in {"unknown", "unrelated"} else 0,
            int(item["evidence_coverage_percent"]),
            int(item["retrieval_relevance"]),
            float((item.get("profile") or {}).get("last_seen") or 0.0),
        ),
        reverse=True,
    )
    for index, item in enumerate(results, start=1):
        item["rank"] = index
    return results


def shortlist_network_profiles(
    visual_profile: dict[str, Any],
    network_profiles: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """High-recall blocking for the expensive LLM review.

    Retrieval scores are intentionally not passed into relationship confidence.
    """
    frequencies = _mutable_term_frequencies(network_profiles)
    total_profiles = max(1, len(network_profiles))
    ranked = [
        (
            _retrieval_score(visual_profile, profile, frequencies, total_profiles),
            _structured_recall_score(visual_profile, profile),
            float(profile.get("last_seen") or 0.0),
            profile,
        )
        for profile in network_profiles
    ]
    ranked.sort(key=lambda item: item[:3], reverse=True)
    return [profile for _, _, _, profile in ranked[: max(0, int(limit))]]


def _compact_prompt_profile(profile: dict[str, Any]) -> dict[str, Any]:
    identifiers = profile.get("identifiers") or {}
    connections = profile.get("connections") or {}
    strong_identifiers: dict[str, list[str]] = {}
    mutable_identifiers: dict[str, list[str]] = {}
    for key, value in identifiers.items():
        values = _string_list(value)
        if not values:
            continue
        target = strong_identifiers if str(key).lower() in _STRONG_IDENTIFIER_KEYS else mutable_identifiers
        target[str(key)] = values[:10]
    for key, value in connections.items():
        values = _string_list(value)
        if values:
            strong_identifiers[str(key)] = values[:8]

    operations = [
        {
            "topic": str(operation.get("topic") or ""),
            "action": str(operation.get("action") or operation.get("name") or ""),
            "accepted_values": _string_list(operation.get("accepted_values"))[:8],
            "source": str(operation.get("source") or ""),
        }
        for operation in (profile.get("operations") or [])
        if isinstance(operation, dict)
    ]
    classification = profile.get("classification") or {}
    return {
        "candidate_id": profile.get("canonical_device_id"),
        "mutable_labels": {
            "display_name": profile.get("display_name"),
            "summary": profile.get("summary"),
            "mqtt_topics": _mqtt_topic_values(profile)[:12],
            "other_identifiers": mutable_identifiers,
        },
        "structured_description": {
            "vendor": profile.get("vendor"),
            "models": profile.get("model_candidates") or [],
            "device_type": profile.get("device_type"),
            "capabilities": profile.get("capabilities") or [],
            "protocols": profile.get("protocols") or [],
            "classification_method": classification.get("method"),
            "metadata_sources": classification.get("metadata_sources") or [],
            "classification_confidence": classification.get("confidence"),
        },
        "stable_identifiers": strong_identifiers,
        "observed_behavior": {
            "data_keys": sorted((profile.get("data") or {}).keys())[:32],
            "operations": operations[:16],
        },
        "deterministic_endpoint_role": _endpoint_role(profile),
    }


def _llm_results_by_candidate(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return result
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        relation = str(candidate.get("relation") or "unknown")
        if not candidate_id or relation not in PAIRING_RELATIONS:
            continue
        verdicts = candidate.get("verdicts")
        if not isinstance(verdicts, list) or len(verdicts) != len(PAIRING_RULES):
            continue
        rules: dict[str, dict[str, str]] = {}
        for rule, verdict in zip(PAIRING_RULES, verdicts):
            token = str(verdict)
            if token in _VALID_VERDICTS:
                rules[str(rule["id"])] = {"verdict": token}
        result[candidate_id] = {"relation": relation, "rules": rules}
    return result


def _heuristic_rule_results(
    visual: dict[str, Any],
    network: dict[str, Any],
    frequencies: Counter[str],
    total_profiles: int,
) -> dict[str, dict[str, Any]]:
    visual_models = _normalized_set(visual.get("model_candidates"))
    network_models = _normalized_set(network.get("model_candidates"))
    visual_vendors = _normalized_set(visual.get("vendor_candidates"))
    network_vendors = _normalized_set([network.get("vendor")])
    visual_caps = _normalized_set(visual.get("capabilities"))
    network_caps = _normalized_set(network.get("capabilities"))
    structured_semantics = _network_semantics_are_structured(network)

    exact_models = visual_models.intersection(network_models)
    model_family = _related_tokens(
        _lexical_terms(visual.get("model_candidates")),
        _lexical_terms(network.get("model_candidates")),
    )
    shared_caps = visual_caps.intersection(network_caps) if structured_semantics else set()
    stable_visual = _identifier_like_terms(visual.get("visible_text"))
    stable_network = _strong_network_identifier_terms(network)
    stable_matches = stable_visual.intersection(stable_network)
    mutable_visual = _lexical_terms(
        [
            *(visual.get("visible_text") or []),
            *(visual.get("vendor_candidates") or []),
            *(visual.get("model_candidates") or []),
        ]
    )
    mutable_network = _mutable_network_terms(network)
    mutable_matches = _related_tokens(mutable_visual, mutable_network)
    rarity = max(
        (_term_rarity(term, frequencies, total_profiles) for term in mutable_matches),
        default=0.0,
    )
    endpoint_role = _endpoint_role(network)
    label_support = bool(mutable_matches)

    return {
        "stable_identifier_support": _decision(
            stable_matches,
            stable_visual and stable_network,
            stable_visual,
            stable_network,
            conflict_when_empty=False,
            reason="Exact identifier-like OCR and stable network identifiers only.",
        ),
        "structured_model_consistency": _decision(
            exact_models or model_family,
            visual_models and network_models,
            visual_models,
            network_models,
            conflict_when_empty=bool(visual_models and network_models),
            reason="Only structured network model fields are compared; names and topics are excluded.",
        ),
        "structured_vendor_consistency": _decision(
            visual_vendors.intersection(network_vendors),
            visual_vendors and network_vendors and _network_vendor_is_explicit(network),
            visual_vendors,
            network_vendors,
            conflict_when_empty=bool(
                visual_vendors and network_vendors and _network_vendor_is_explicit(network)
            ),
            reason="Network vendor claims are used only when backed by discovery metadata.",
        ),
        "device_class_consistency": _type_decision(visual, network),
        "capability_consistency": _decision(
            shared_caps,
            visual_caps and network_caps and structured_semantics,
            visual_caps,
            network_caps,
            conflict_when_empty=False,
            reason="Capabilities from weak keyword classification are not deterministic evidence.",
        ),
        "telemetry_consistency": {
            "verdict": "unknown",
            "visual_evidence": ", ".join(sorted(visual_caps)),
            "network_evidence": ", ".join(_behavior_keys(network)[:24]),
            "reason": "Semantic interpretation of telemetry is delegated to the LLM evidence extractor.",
        },
        "mutable_label_support": {
            "verdict": "match" if label_support else "unknown",
            "visual_evidence": ", ".join(sorted(mutable_visual)),
            "network_evidence": ", ".join(sorted(mutable_matches)),
            "reason": (
                f"Mutable label overlap; corpus rarity={rarity:.2f}. It supports retrieval or a relation, not identity."
                if label_support
                else "No meaningful mutable label overlap."
            ),
            "rarity": rarity,
        },
        "endpoint_role_consistency": {
            "verdict": "match" if label_support and endpoint_role != "device_or_unknown" else "unknown",
            "visual_evidence": str(visual.get("device_type") or ""),
            "network_evidence": endpoint_role,
            "reason": (
                "Endpoint capabilities and mutable label support an attached-device relationship."
                if label_support and endpoint_role != "device_or_unknown"
                else "Endpoint role is insufficient to establish a relationship."
            ),
        },
    }


def _merge_rule_decision(
    rule_id: str,
    deterministic: dict[str, Any],
    llm_decision: dict[str, Any] | None,
    visual: dict[str, Any],
    network: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if not llm_decision:
        return dict(deterministic), "deterministic"

    deterministic_verdict = str(deterministic.get("verdict") or "unknown")
    llm_verdict = str(llm_decision.get("verdict") or "unknown")
    strong_deterministic_rules = {
        "stable_identifier_support",
        "structured_model_consistency",
        "structured_vendor_consistency",
    }
    if rule_id in strong_deterministic_rules and deterministic_verdict != "unknown":
        return dict(deterministic), "deterministic_guard"

    if llm_verdict == "conflict" and not _llm_conflict_allowed(rule_id, visual, network):
        decision = dict(deterministic)
        if deterministic_verdict == "unknown":
            decision["reason"] = "LLM conflict downgraded because the compared source is partial or mutable."
        return decision, "llm_guarded"

    decision = dict(deterministic)
    decision["verdict"] = llm_verdict if llm_verdict in _VALID_VERDICTS else deterministic_verdict
    return decision, "llm"


def _llm_conflict_allowed(rule_id: str, visual: dict[str, Any], network: dict[str, Any]) -> bool:
    if rule_id == "stable_identifier_support":
        return bool(
            _identifier_like_terms(visual.get("visible_text"))
            and _strong_network_identifier_terms(network)
        )
    if rule_id == "structured_model_consistency":
        return bool(visual.get("model_candidates") and network.get("model_candidates"))
    if rule_id == "structured_vendor_consistency":
        return bool(visual.get("vendor_candidates") and _network_vendor_is_explicit(network))
    if rule_id in {"mutable_label_support", "capability_consistency"}:
        return False
    return True


def _score_rule_dimension(rules: list[dict[str, Any]], dimension: str) -> int:
    support = 0.0
    conflicts = 0.0
    for result in rules:
        rule = _RULES_BY_ID[str(result["rule_id"])]
        verdict = str(result.get("verdict") or "unknown")
        weight = float(rule[dimension])
        if verdict == "match":
            if result["rule_id"] == "mutable_label_support":
                match = re.search(
                    r"rarity=([0-9]+(?:\.[0-9]+)?)",
                    str(result.get("reason") or ""),
                )
                weight *= float(match.group(1)) if match else 0.5
            support += weight
        elif verdict == "conflict":
            if dimension == "relation_weight" and result["rule_id"] not in {
                "telemetry_consistency",
                "endpoint_role_consistency",
            }:
                continue
            conflicts += abs(float(rule["conflict"]))
    return max(0, min(95, int(round(support - conflicts))))


def _select_relation(
    visual: dict[str, Any],
    network: dict[str, Any],
    rules: list[dict[str, Any]],
    llm_relation: str,
    identity_score: int,
) -> str:
    role = _endpoint_role(network)
    verdicts = {str(rule["rule_id"]): str(rule["verdict"]) for rule in rules}
    label_support = verdicts.get("mutable_label_support") == "match"
    has_data = bool(network.get("data"))
    has_operations = bool(network.get("operations"))

    if identity_score >= 60 and role == "device_or_unknown":
        return "same_physical_device"

    if llm_relation in PAIRING_RELATIONS:
        if llm_relation == "same_physical_device":
            if role == "device_or_unknown" and identity_score >= 25:
                return llm_relation
        elif llm_relation == "powers" and role == "power_endpoint":
            return llm_relation
        elif llm_relation == "controls" and has_operations:
            return llm_relation
        elif llm_relation == "monitors" and has_data:
            return llm_relation
        elif llm_relation == "gateway_for" and role == "gateway":
            return llm_relation
        elif llm_relation in {"related", "unrelated"}:
            return llm_relation

    if role == "power_endpoint" and label_support:
        return "powers"
    if role == "control_endpoint" and label_support:
        return "controls"
    if role == "monitor_endpoint" and label_support:
        return "monitors"
    if role == "gateway" and label_support:
        return "gateway_for"
    if identity_score >= 35 and role == "device_or_unknown":
        return "same_physical_device"
    if label_support and has_operations:
        return "controls"
    if label_support and has_data:
        return "monitors"
    if label_support:
        return "related"
    return "unknown"


def _relation_confidence(
    relation: str,
    identity_score: int,
    relation_score: int,
    llm_reviewed: bool,
) -> int:
    if relation == "same_physical_device":
        return identity_score
    if relation == "unrelated":
        return min(90, max(identity_score, relation_score))
    if relation == "unknown":
        return 0
    bonus = 6 if llm_reviewed else 0
    return min(90, relation_score + bonus)


def _cap_identity_confidence(score: int, rules: list[dict[str, Any]]) -> int:
    verdicts = {str(rule["rule_id"]): str(rule["verdict"]) for rule in rules}
    if verdicts.get("stable_identifier_support") == "match":
        return score
    if verdicts.get("structured_model_consistency") == "match":
        return min(score, 75)
    # Class, behavior, and names can identify a plausible endpoint class, but
    # cannot distinguish one physical instance from several similar devices.
    return min(score, 45)


def _evidence_coverage(rules: list[dict[str, Any]]) -> int:
    known = sum(
        int(result.get("max_points") or 0)
        for result in rules
        if result.get("verdict") != "unknown"
    )
    total = sum(max(int(rule["identity_weight"]), int(rule["relation_weight"])) for rule in PAIRING_RULES)
    return max(0, min(100, int(round(100.0 * known / max(1, total)))))


def _evidence_summaries(rules: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    support = [
        str(rule["label"])
        for rule in rules
        if rule.get("verdict") == "match"
    ]
    conflicts = [
        str(rule["label"])
        for rule in rules
        if rule.get("verdict") == "conflict"
    ]
    return support[:5], conflicts[:5]


def _confidence_level(score: int) -> str:
    if score >= 80:
        return "strong"
    if score >= 60:
        return "likely"
    if score >= 40:
        return "possible"
    if score >= 20:
        return "weak"
    return "insufficient"


def _legacy_rule_points(rule: dict[str, Any], verdict: str) -> int:
    if verdict == "match":
        return max(int(rule["identity_weight"]), int(rule["relation_weight"]))
    if verdict == "conflict":
        return int(rule["conflict"])
    return 0


def _type_decision(visual: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    visual_type = _normalize_token(visual.get("device_type"))
    network_type = _normalize_token(network.get("device_type"))
    structured = _network_semantics_are_structured(network)
    if not visual_type or not network_type or "networkdevice" in {visual_type, network_type}:
        verdict = "unknown"
    elif visual_type == network_type or visual_type in network_type or network_type in visual_type:
        verdict = "match" if structured else "unknown"
    elif structured:
        verdict = "conflict"
    else:
        verdict = "unknown"
    return {
        "verdict": verdict,
        "visual_evidence": str(visual.get("device_type") or ""),
        "network_evidence": str(network.get("device_type") or ""),
        "reason": "Weak keyword-derived network classes remain unknown; structured classes may support or conflict.",
    }


def _decision(
    shared: set[str],
    comparable: object,
    visual_values: set[str],
    network_values: set[str],
    *,
    conflict_when_empty: bool,
    reason: str,
) -> dict[str, Any]:
    if shared:
        verdict = "match"
    elif comparable and conflict_when_empty:
        verdict = "conflict"
    else:
        verdict = "unknown"
    return {
        "verdict": verdict,
        "visual_evidence": ", ".join(sorted(visual_values)),
        "network_evidence": ", ".join(sorted(network_values)),
        "reason": reason,
    }


def _structured_recall_score(visual: dict[str, Any], network: dict[str, Any]) -> int:
    score = 0
    if _normalized_set(visual.get("model_candidates")).intersection(
        _normalized_set(network.get("model_candidates"))
    ):
        score += 40
    if _normalized_set(visual.get("vendor_candidates")).intersection(
        _normalized_set([network.get("vendor")])
    ):
        score += 15
    if _normalize_token(visual.get("device_type")) == _normalize_token(network.get("device_type")):
        score += 20
    return score


def _retrieval_score(
    visual: dict[str, Any],
    network: dict[str, Any],
    frequencies: Counter[str],
    total_profiles: int,
) -> int:
    weighted_terms: dict[str, int] = {}
    for key, weight in (
        ("visible_text", 24),
        ("model_candidates", 22),
        ("vendor_candidates", 16),
        ("device_type", 10),
        ("capabilities", 8),
    ):
        for term in _lexical_terms(visual.get(key)):
            weighted_terms[term] = max(weighted_terms.get(term, 0), weight)
    network_terms = _all_network_retrieval_terms(network)
    score = 0.0
    for term, weight in weighted_terms.items():
        if _related_tokens({term}, network_terms):
            score += weight * _term_rarity(term, frequencies, total_profiles)
    score += _structured_recall_score(visual, network)
    return min(100, int(round(score)))


def _mutable_term_frequencies(profiles: list[dict[str, Any]]) -> Counter[str]:
    frequencies: Counter[str] = Counter()
    for profile in profiles:
        frequencies.update(_mutable_network_terms(profile))
    return frequencies


def _term_rarity(term: str, frequencies: Counter[str], total_profiles: int) -> float:
    count = max(1, int(frequencies.get(term, 1)))
    numerator = math.log((total_profiles + 1.0) / (count + 1.0)) + 1.0
    denominator = math.log(total_profiles + 1.0) + 1.0
    return max(0.2, min(1.0, numerator / denominator))


def _network_semantics_are_structured(profile: dict[str, Any]) -> bool:
    """A network profile is structured evidence if its semantics came from live
    discovery rather than keyword guessing. Explicit metadata sources are the
    strongest marker, but a profile that actually carries observed behaviour
    (telemetry fields or operations) is itself behaviour-derived capability
    evidence and is treated as structured. This honours the thesis claim that
    behaviour is evidence of capability, while still excluding profiles whose
    sole semantics are a keyword-derived display name."""
    classification = profile.get("classification") or {}
    sources = classification.get("metadata_sources") or []
    method = str(classification.get("method") or "").lower()
    confidence = float(classification.get("confidence") or 0.0)
    if bool(sources) or ("explicit" in method and confidence >= 0.8):
        return True
    # Observed behaviour (data fields or operations) is structured evidence.
    return bool(profile.get("data")) or bool(profile.get("operations"))


def _network_vendor_is_explicit(profile: dict[str, Any]) -> bool:
    classification = profile.get("classification") or {}
    return bool(classification.get("metadata_sources"))


def _endpoint_role(profile: dict[str, Any]) -> str:
    device_type = _normalize_token(profile.get("device_type"))
    capabilities = _normalized_set(profile.get("capabilities"))
    if "gateway" in device_type or "bridge" in device_type:
        return "gateway"
    if device_type in _CONTROL_DEVICE_TYPES or (
        capabilities and capabilities.issubset(_POWER_CAPABILITIES)
    ):
        return "power_endpoint"
    if "controller" in device_type or "remotecontrol" in device_type:
        return "control_endpoint"
    if "monitor" in device_type:
        return "monitor_endpoint"
    return "device_or_unknown"


def _strong_network_identifier_terms(profile: dict[str, Any]) -> set[str]:
    values: list[object] = []
    for mapping_name in ("identifiers", "connections"):
        mapping = profile.get(mapping_name) or {}
        if not isinstance(mapping, dict):
            continue
        for key, raw in mapping.items():
            if str(key).lower() not in _STRONG_IDENTIFIER_KEYS:
                continue
            values.extend(raw if isinstance(raw, list) else [raw])
    return _identifier_like_terms(values)


def _identifier_like_terms(values: object) -> set[str]:
    terms = _lexical_terms(values)
    return {
        term
        for term in terms
        if len(term) >= 6 and any(char.isdigit() for char in term)
    }


def _mutable_network_terms(profile: dict[str, Any]) -> set[str]:
    values: list[object] = [
        profile.get("display_name"),
        profile.get("summary"),
        *_mqtt_topic_values(profile),
    ]
    return _lexical_terms(values)


def _all_network_retrieval_terms(profile: dict[str, Any]) -> set[str]:
    values: list[object] = [
        profile.get("display_name"),
        profile.get("summary"),
        profile.get("vendor"),
        profile.get("device_type"),
        *(profile.get("model_candidates") or []),
        *(profile.get("capabilities") or []),
        *_mqtt_topic_values(profile),
        *_behavior_keys(profile),
    ]
    return _lexical_terms(values)


def _mqtt_topic_values(profile: dict[str, Any]) -> list[str]:
    values: list[str] = []
    identifiers = profile.get("identifiers") or {}
    if isinstance(identifiers, dict):
        for key, raw in identifiers.items():
            if "mqtt" not in str(key).lower() and "topic" not in str(key).lower():
                continue
            values.extend(_string_list(raw))
    for operation in profile.get("operations") or []:
        if isinstance(operation, dict) and operation.get("topic"):
            values.append(str(operation["topic"]))
    return values


def _behavior_keys(profile: dict[str, Any]) -> list[str]:
    values = [str(key) for key in (profile.get("data") or {}).keys()]
    for operation in profile.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        for key in ("name", "action", "sensor_key", "topic"):
            if operation.get(key):
                values.append(str(operation[key]))
    return values


def _lexical_terms(values: object) -> set[str]:
    if values is None:
        return set()
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    terms: set[str] = set()
    for value in values:
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
        for segment in re.findall(r"[A-Za-z]+[0-9]+[A-Za-z0-9]*|[A-Za-z]{2,}", text):
            token = _normalize_token(segment)
            if len(token) >= 2 and token not in _GENERIC_RETRIEVAL_TERMS:
                terms.add(token)
            stem = re.fullmatch(r"([a-z]{4,})[0-9]+", token)
            if stem:
                terms.add(stem.group(1))
    return terms


def _related_tokens(left_values: set[str], right_values: set[str]) -> set[str]:
    matches: set[str] = set()
    for left in left_values:
        for right in right_values:
            if left == right or (
                min(len(left), len(right)) >= 4
                and (left in right or right in left)
            ):
                matches.add(left)
                break
    return matches


def _normalized_set(values: object) -> set[str]:
    if values is None:
        return set()
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return {_normalize_token(value) for value in values if _normalize_token(value)}


def _normalize_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
