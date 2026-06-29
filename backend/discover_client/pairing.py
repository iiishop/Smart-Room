"""Explainable matching between a visual device profile and network profiles."""

from __future__ import annotations

import json
import re
from typing import Any


PAIRING_RULES: list[dict[str, Any]] = [
    {"id": "exact_model_match", "label": "Exact model", "weight": 20, "conflict": -30},
    {"id": "model_family_match", "label": "Model family", "weight": 15, "conflict": -10},
    {"id": "vendor_match", "label": "Vendor", "weight": 10, "conflict": -15},
    {"id": "device_type_match", "label": "Device type", "weight": 10, "conflict": -15},
    {"id": "core_capability_match", "label": "Core capabilities", "weight": 10, "conflict": -10},
    {"id": "secondary_capability_match", "label": "Secondary capabilities", "weight": 5, "conflict": -5},
    {"id": "visible_identifier_support", "label": "Visible/network identifier", "weight": 20, "conflict": -10},
    {"id": "physical_function_consistency", "label": "Physical/function consistency", "weight": 10, "conflict": -10},
]

_RULES_BY_ID = {rule["id"]: rule for rule in PAIRING_RULES}
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
    compact_candidates = []
    for profile in network_profiles:
        identifiers = {
            str(key): _string_list(value)[:12]
            for key, value in (profile.get("identifiers") or {}).items()
            if _string_list(value)
        }
        connections = {
            str(key): _string_list(value)[:8]
            for key, value in (profile.get("connections") or {}).items()
            if _string_list(value)
        }
        data_keys = sorted((profile.get("data") or {}).keys())
        operations = [
            {
                "name": operation.get("name") or operation.get("topic") or operation.get("key"),
                "topic": operation.get("topic"),
            }
            for operation in (profile.get("operations") or [])
            if isinstance(operation, dict)
        ]
        compact_candidates.append(
            {
                "candidate_id": profile.get("canonical_device_id"),
                "display_name": profile.get("display_name"),
                "summary": profile.get("summary"),
                "vendor": profile.get("vendor"),
                "model_candidates": profile.get("model_candidates") or [],
                "device_type": profile.get("device_type"),
                "capabilities": profile.get("capabilities") or [],
                "protocols": profile.get("protocols") or [],
                "identifiers": identifiers,
                "connections": connections,
                "observed_data_count": len(data_keys),
                "observed_data_keys": data_keys[:24],
                "observed_operation_count": len(operations),
                "observed_operations": operations[:16],
                "endpoint_role": (
                    "control_or_monitor_adapter"
                    if _is_indirect_control_endpoint(profile)
                    else "device_or_unknown"
                ),
            }
        )
    rule_ids = [rule["id"] for rule in PAIRING_RULES]
    return (
        "Compare one visually observed physical device with every discovered network device. "
        "Evaluate every rule independently. Use only supplied evidence. Missing evidence is unknown, not a match. "
        "Observed MQTT capabilities are partial evidence rather than proof of the device's complete type. "
        "A network candidate can be a controller, smart plug, monitor, or gateway attached to the physical device; "
        "in that case its own manufacturer/model/type differing from the physical device is unknown, not a conflict. "
        "A meaningful visual vendor/model/name term appearing in a network display name or topic (including a "
        "numbered instance suffix) supports model-family and identifier rules. Device-specific telemetry or "
        "operations can support physical/function consistency even when the network profile type is incomplete. "
        "Similar device type does not prove that two same-model physical instances are identical. "
        "Do not calculate totals, percentages, probabilities, rankings, display names, summaries, or explanations; "
        "deterministic code will score and explain the rules. Return one verdict for every supplied rule and candidate.\n\n"
        "VISUAL_PROFILE:\n"
        + json.dumps(visual_profile, ensure_ascii=False, separators=(",", ":"))
        + "\n\nNETWORK_CANDIDATES:\n"
        + json.dumps(compact_candidates, ensure_ascii=False, separators=(",", ":"))
        + "\n\nRULE_IDS:\n"
        + json.dumps(rule_ids, ensure_ascii=False, separators=(",", ":"))
        + "\n\nReturn exactly one JSON object:\n"
        '{"candidates":[{"candidate_id":"candidate id","verdicts":'
        '["unknown","unknown","unknown","unknown","unknown","unknown","unknown","unknown"]}]}'
    )


def pairing_response_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "smart_room_pairing_review",
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
                            "required": ["candidate_id", "verdicts"],
                            "properties": {
                                "candidate_id": {"type": "string"},
                                "verdicts": {
                                    "type": "array",
                                    "minItems": len(PAIRING_RULES),
                                    "maxItems": len(PAIRING_RULES),
                                    "items": {
                                        "type": "string",
                                        "enum": sorted(_VALID_VERDICTS),
                                    },
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
    llm_by_candidate = _llm_results_by_candidate(llm_payload)
    results: list[dict[str, Any]] = []
    for profile in network_profiles:
        candidate_id = str(profile.get("canonical_device_id") or "")
        heuristic = _heuristic_rule_results(visual_profile, profile)
        llm_result = llm_by_candidate.get(candidate_id, {})
        incoming = llm_result.get("rules", {})
        rules: list[dict[str, Any]] = []
        raw_score = 0
        coverage = 0
        for rule in PAIRING_RULES:
            rule_id = str(rule["id"])
            deterministic = heuristic[rule_id]
            llm_decision = incoming.get(rule_id)
            decision, decision_source = _merge_rule_decision(
                rule_id,
                deterministic,
                llm_decision,
                visual_profile,
                profile,
            )
            verdict = str(decision.get("verdict") or "unknown").lower()
            if verdict not in _VALID_VERDICTS:
                verdict = "unknown"
            points = int(rule["weight"]) if verdict == "match" else int(rule["conflict"]) if verdict == "conflict" else 0
            if verdict != "unknown":
                coverage += int(rule["weight"])
            raw_score += points
            rules.append(
                {
                    "rule_id": rule_id,
                    "label": rule["label"],
                    "verdict": verdict,
                    "points": points,
                    "max_points": int(rule["weight"]),
                    "visual_evidence": str(decision.get("visual_evidence") or ""),
                    "network_evidence": str(decision.get("network_evidence") or ""),
                    "reason": str(decision.get("reason") or ""),
                    "source": decision_source,
                }
            )
        score = max(0, min(100, raw_score))
        result = {
            "canonical_device_id": candidate_id,
            "display_name": llm_result.get("display_name_zh") or profile.get("display_name") or candidate_id,
            "summary": llm_result.get("summary_zh") or profile.get("summary") or "",
            "score": score,
            "confidence_percent": score,
            "evidence_coverage_percent": max(0, min(100, coverage)),
            "rules": rules,
            "profile": profile,
        }
        results.append(result)
    results.sort(
        key=lambda item: (
            int(item["score"]),
            int(item["evidence_coverage_percent"]),
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
    """Recall likely candidates before the expensive LLM review.

    The scorer alone cannot rank sparse MQTT profiles reliably because absence of
    capabilities is unknown. This field-aware lexical stage preserves explicit
    names, models, labels, and topic identifiers without reviewing every device.
    """
    baseline_by_id = {
        str(candidate.get("canonical_device_id") or ""): candidate
        for candidate in score_candidates(visual_profile, network_profiles)
    }
    ranked: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    for profile in network_profiles:
        candidate_id = str(profile.get("canonical_device_id") or "")
        baseline = baseline_by_id.get(candidate_id, {})
        retrieval_score = _retrieval_score(visual_profile, profile)
        rank_key = (
            float(retrieval_score + int(baseline.get("score") or 0)),
            float(retrieval_score),
            float(int(baseline.get("score") or 0)),
            float(int(baseline.get("evidence_coverage_percent") or 0)),
            float(profile.get("last_seen") or 0.0),
        )
        ranked.append((rank_key, profile))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [profile for _, profile in ranked[: max(0, int(limit))]]


def _llm_results_by_candidate(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return result
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            continue
        decisions: dict[str, dict[str, Any]] = {}
        verdicts = candidate.get("verdicts")
        if isinstance(verdicts, list) and len(verdicts) == len(PAIRING_RULES):
            for rule, verdict in zip(PAIRING_RULES, verdicts):
                if str(verdict) in _VALID_VERDICTS:
                    decisions[str(rule["id"])] = {"verdict": str(verdict)}
        for decision in candidate.get("rules") or []:
            if not isinstance(decision, dict):
                continue
            rule_id = str(decision.get("rule_id") or "")
            if rule_id in _RULES_BY_ID:
                decisions[rule_id] = decision
        result[candidate_id] = {
            "display_name_zh": str(candidate.get("display_name_zh") or "").strip(),
            "summary_zh": str(candidate.get("summary_zh") or "").strip(),
            "rules": decisions,
        }
    return result


def _heuristic_rule_results(visual: dict[str, Any], network: dict[str, Any]) -> dict[str, dict[str, Any]]:
    visual_models = _normalized_set(visual.get("model_candidates"))
    network_models = _normalized_set(network.get("model_candidates"))
    visual_vendors = _normalized_set(visual.get("vendor_candidates"))
    network_vendors = _normalized_set([network.get("vendor")])
    visual_caps = _normalized_set(visual.get("capabilities"))
    network_caps = _normalized_set(network.get("capabilities"))
    visible = _normalized_set(visual.get("visible_text"))
    visual_model_terms = _lexical_terms(visual.get("model_candidates"))
    visual_identity_terms = _lexical_terms(
        [
            *(visual.get("visible_text") or []),
            *(visual.get("vendor_candidates") or []),
            *(visual.get("model_candidates") or []),
        ]
    )
    network_identity_terms = _network_identity_terms(network)

    exact_models = visual_models.intersection(network_models)
    model_family = _related_tokens(
        visual_model_terms or visual_models,
        network_identity_terms,
    )
    shared_caps = visual_caps.intersection(network_caps)
    core_caps = {"temperature", "humidity", "motion", "power", "energy", "camera", "airquality", "contact"}
    shared_core = shared_caps.intersection(core_caps)
    visible_support = _related_tokens(
        _lexical_terms(visual.get("visible_text")) or visible,
        network_identity_terms,
    )
    identifier_support = _related_tokens(visual_identity_terms, network_identity_terms)
    has_endpoint_evidence = bool(network.get("data") or network.get("operations"))
    indirect_endpoint = _is_indirect_control_endpoint(network)
    function_consistent = bool(
        shared_caps
        or (
            identifier_support
            and (has_endpoint_evidence or indirect_endpoint)
        )
    )

    return {
        "exact_model_match": _decision(
            exact_models,
            visual_models and network_models,
            visual_models,
            network_models,
            conflict_when_empty=not indirect_endpoint,
        ),
        "model_family_match": _decision(model_family, visual_models and network_models, visual_models, network_models),
        "vendor_match": _decision(
            visual_vendors.intersection(network_vendors),
            visual_vendors and network_vendors,
            visual_vendors,
            network_vendors,
            conflict_when_empty=_network_vendor_is_explicit(network) and not indirect_endpoint,
        ),
        "device_type_match": _type_decision(visual, network),
        "core_capability_match": _decision(
            shared_core,
            visual_caps and network_caps,
            visual_caps,
            network_caps,
            conflict_when_empty=False,
        ),
        "secondary_capability_match": _decision(
            shared_caps - shared_core,
            visual_caps and network_caps,
            visual_caps,
            network_caps,
            conflict_when_empty=False,
        ),
        "visible_identifier_support": _decision(
            visible_support,
            visible and network_identity_terms,
            visible,
            network_identity_terms,
            conflict_when_empty=False,
        ),
        "physical_function_consistency": {
            "verdict": "match" if function_consistent else "unknown",
            "visual_evidence": ", ".join(sorted(_normalized_set(visual.get("physical_features")))),
            "network_evidence": ", ".join(sorted(network_caps | identifier_support)),
            "reason": (
                "Shared functions or a named endpoint with runtime evidence support the physical role."
                if function_consistent
                else "Insufficient structured physical evidence."
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
        return dict(deterministic), "deterministic_fallback"

    llm_verdict = str(llm_decision.get("verdict") or "unknown").lower()
    deterministic_verdict = str(deterministic.get("verdict") or "unknown").lower()
    if deterministic_verdict == "match" and llm_verdict != "match":
        guarded = dict(deterministic)
        guarded["reason"] = (
            str(guarded.get("reason") or "")
            + " Strong normalized evidence was retained over an incompatible LLM verdict."
        ).strip()
        return guarded, "deterministic_guard"

    decision = dict(deterministic)
    decision.update(llm_decision)
    if llm_verdict == "conflict" and not _conflict_is_supported(rule_id, visual, network):
        decision["verdict"] = "unknown"
        decision["reason"] = (
            "LLM conflict downgraded to unknown because the network observation is partial "
            "or represents an attached control/monitor endpoint."
        )
        return decision, "llm_guarded_unknown"
    return decision, "llm"


def _conflict_is_supported(rule_id: str, visual: dict[str, Any], network: dict[str, Any]) -> bool:
    if rule_id in {
        "core_capability_match",
        "secondary_capability_match",
        "device_type_match",
        "visible_identifier_support",
    }:
        return False
    if rule_id in {"exact_model_match", "model_family_match"}:
        return bool(
            _normalized_set(visual.get("model_candidates"))
            and _normalized_set(network.get("model_candidates"))
            and not _is_indirect_control_endpoint(network)
        )
    if rule_id == "vendor_match":
        return bool(
            network.get("vendor")
            and _network_vendor_is_explicit(network)
            and not _is_indirect_control_endpoint(network)
        )
    return rule_id == "physical_function_consistency"


def _type_decision(visual: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    visual_type = _normalize_token(visual.get("device_type"))
    network_type = _normalize_token(network.get("device_type"))
    if not visual_type or not network_type or "networkdevice" in {visual_type, network_type}:
        verdict = "unknown"
    elif visual_type == network_type or visual_type in network_type or network_type in visual_type:
        verdict = "match"
    else:
        verdict = "unknown"
    return {
        "verdict": verdict,
        "visual_evidence": str(visual.get("device_type") or ""),
        "network_evidence": str(network.get("device_type") or ""),
        "reason": (
            "Deterministic normalized device-type comparison. Different inferred types are not a conflict "
            "because visual and network observations may both be incomplete."
        ),
    }


def _decision(
    shared: set[str],
    comparable: object,
    visual_values: set[str],
    network_values: set[str],
    *,
    conflict_when_empty: bool = True,
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
        "reason": "Deterministic normalized evidence comparison.",
    }


def _network_tokens(profile: dict[str, Any]) -> set[str]:
    values: list[object] = [
        profile.get("display_name"),
        profile.get("summary"),
        profile.get("vendor"),
        *(profile.get("model_candidates") or []),
    ]
    for group in (profile.get("identifiers") or {}).values():
        values.extend(group if isinstance(group, list) else [])
    for group in (profile.get("connections") or {}).values():
        values.extend(group if isinstance(group, list) else [])
    tokens: set[str] = set()
    for value in values:
        text = str(value or "")
        tokens.add(_normalize_token(text))
        tokens.update(_normalize_token(part) for part in re.split(r"[/_.:\s-]+", text))
    tokens.discard("")
    return tokens


def _network_identity_terms(profile: dict[str, Any]) -> set[str]:
    values: list[object] = [
        profile.get("display_name"),
        profile.get("summary"),
        profile.get("vendor"),
        *(profile.get("model_candidates") or []),
    ]
    for group in (profile.get("identifiers") or {}).values():
        if isinstance(group, list):
            values.extend(group)
    return _lexical_terms(values) | _network_tokens(profile)


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


def _retrieval_score(visual: dict[str, Any], network: dict[str, Any]) -> int:
    weighted_terms: dict[str, int] = {}
    for key, weight in (
        ("visible_text", 24),
        ("model_candidates", 22),
        ("vendor_candidates", 16),
    ):
        for term in _lexical_terms(visual.get(key)):
            weighted_terms[term] = max(weighted_terms.get(term, 0), weight)

    network_terms = _network_identity_terms(network)
    score = sum(
        weight
        for term, weight in weighted_terms.items()
        if _related_tokens({term}, network_terms)
    )
    visual_type = _normalize_token(visual.get("device_type"))
    network_type = _normalize_token(network.get("device_type"))
    if visual_type and network_type and visual_type == network_type:
        score += 10
    score += min(
        12,
        4
        * len(
            _normalized_set(visual.get("capabilities"))
            .intersection(_normalized_set(network.get("capabilities")))
        ),
    )
    return min(100, score)


def _is_indirect_control_endpoint(profile: dict[str, Any]) -> bool:
    device_type = _normalize_token(profile.get("device_type"))
    capabilities = _normalized_set(profile.get("capabilities"))
    control_capabilities = {
        "current",
        "energy",
        "power",
        "relay",
        "switch",
        "voltage",
    }
    if device_type in {"energymonitor", "smartplug", "smartswitch"}:
        return True
    return bool(capabilities and capabilities.issubset(control_capabilities))


def _network_vendor_is_explicit(profile: dict[str, Any]) -> bool:
    classification = profile.get("classification") or {}
    return bool(classification.get("metadata_sources"))


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
