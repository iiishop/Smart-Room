"""Explainable matching between a visual device profile and network profiles."""

from __future__ import annotations

import json
import re
from typing import Any


PAIRING_RULES: list[dict[str, Any]] = [
    {"id": "exact_model_match", "label": "Exact model", "weight": 25, "conflict": -35},
    {"id": "model_family_match", "label": "Model family", "weight": 10, "conflict": -12},
    {"id": "vendor_match", "label": "Vendor", "weight": 10, "conflict": -20},
    {"id": "device_type_match", "label": "Device type", "weight": 15, "conflict": -25},
    {"id": "core_capability_match", "label": "Core capabilities", "weight": 15, "conflict": -15},
    {"id": "secondary_capability_match", "label": "Secondary capabilities", "weight": 5, "conflict": -5},
    {"id": "visible_identifier_support", "label": "Visible/network identifier", "weight": 10, "conflict": -15},
    {"id": "physical_function_consistency", "label": "Physical/function consistency", "weight": 10, "conflict": -10},
]

_RULES_BY_ID = {rule["id"]: rule for rule in PAIRING_RULES}
_VALID_VERDICTS = {"match", "conflict", "unknown"}


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
                "identifiers": profile.get("identifiers") or {},
                "connections": profile.get("connections") or {},
                "data_keys": sorted((profile.get("data") or {}).keys()),
            }
        )
    rules = [
        {
            "rule_id": rule["id"],
            "meaning": rule["label"],
            "instruction": "Return match, conflict, or unknown. Do not calculate a score.",
        }
        for rule in PAIRING_RULES
    ]
    return (
        "Compare one visually observed physical device with every discovered network device. "
        "Evaluate every rule independently. Use only supplied evidence. Missing evidence is unknown, "
        "not a match. Similar device type does not prove that two same-model physical instances are identical. "
        "Do not calculate totals, percentages, probabilities, or rankings; deterministic code will score the rules.\n\n"
        "VISUAL_PROFILE:\n"
        + json.dumps(visual_profile, ensure_ascii=False, indent=2)
        + "\n\nNETWORK_CANDIDATES:\n"
        + json.dumps(compact_candidates, ensure_ascii=False, indent=2)
        + "\n\nRULES:\n"
        + json.dumps(rules, ensure_ascii=False, indent=2)
        + "\n\nReturn exactly one JSON object:\n"
        '{"candidates":[{"candidate_id":"...","display_name_zh":"grounded concise name",'
        '"summary_zh":"grounded network-device summary","rules":[{"rule_id":"...",'
        '"verdict":"match|conflict|unknown","visual_evidence":"...",'
        '"network_evidence":"...","reason":"..."}]}]}'
    )


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
            decision = incoming.get(rule_id) or heuristic[rule_id]
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
                    "source": "llm" if rule_id in incoming else "deterministic_fallback",
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
    network_tokens = _network_tokens(network)

    exact_models = visual_models.intersection(network_models)
    model_family = {
        left
        for left in visual_models
        for right in network_models
        if left and right and (left in right or right in left)
    }
    shared_caps = visual_caps.intersection(network_caps)
    core_caps = {"temperature", "humidity", "motion", "power", "energy", "camera", "airquality", "contact"}
    shared_core = shared_caps.intersection(core_caps)
    visible_support = visible.intersection(network_tokens)

    return {
        "exact_model_match": _decision(exact_models, visual_models and network_models, visual_models, network_models),
        "model_family_match": _decision(model_family, visual_models and network_models, visual_models, network_models),
        "vendor_match": _decision(
            visual_vendors.intersection(network_vendors),
            visual_vendors and network_vendors,
            visual_vendors,
            network_vendors,
        ),
        "device_type_match": _type_decision(visual, network),
        "core_capability_match": _decision(shared_core, visual_caps and network_caps, visual_caps, network_caps),
        "secondary_capability_match": _decision(
            shared_caps - shared_core,
            visual_caps and network_caps,
            visual_caps,
            network_caps,
            conflict_when_empty=False,
        ),
        "visible_identifier_support": _decision(
            visible_support,
            visible and network_tokens,
            visible,
            network_tokens,
            conflict_when_empty=False,
        ),
        "physical_function_consistency": {
            "verdict": "match" if shared_caps else "unknown",
            "visual_evidence": ", ".join(sorted(_normalized_set(visual.get("physical_features")))),
            "network_evidence": ", ".join(sorted(network_caps)),
            "reason": "Shared functions support the observed physical role." if shared_caps else "Insufficient structured physical evidence.",
        },
    }


def _type_decision(visual: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    visual_type = _normalize_token(visual.get("device_type"))
    network_type = _normalize_token(network.get("device_type"))
    if not visual_type or not network_type or "networkdevice" in {visual_type, network_type}:
        verdict = "unknown"
    elif visual_type == network_type or visual_type in network_type or network_type in visual_type:
        verdict = "match"
    else:
        verdict = "conflict"
    return {
        "verdict": verdict,
        "visual_evidence": str(visual.get("device_type") or ""),
        "network_evidence": str(network.get("device_type") or ""),
        "reason": "Deterministic normalized device-type comparison.",
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
