"""Persistent mapping from volatile discovery evidence to stable device identities."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from discover_client.identification.device import Device
from discover_client.mqtt_entity import group_physical_devices
from discover_client.profile import identity_aliases


class PersistentDeviceRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()
        self._dirty = False
        self._last_saved_at = 0.0

    def resolve(self, device: Device, *, save: bool = True) -> str:
        aliases = identity_aliases(device)
        with self._lock:
            matched = {
                str(self._data["aliases"].get(_alias_key(alias_type, value)) or "")
                for alias_type, value in aliases
            }
            matched.discard("")
            if matched:
                canonical_id = min(
                    matched,
                    key=lambda item: float(self._data["devices"].get(item, {}).get("created_at", 0.0)),
                )
                for duplicate_id in matched - {canonical_id}:
                    self._merge(canonical_id, duplicate_id)
            else:
                canonical_id = f"urn:smartroom:device:{uuid.uuid4()}"
                self._data["devices"][canonical_id] = {
                    "canonical_device_id": canonical_id,
                    "created_at": time.time(),
                    "last_seen": device.last_seen,
                    "user_name": "",
                    "profile": {},
                }

            for alias_type, value in aliases:
                self._data["aliases"][_alias_key(alias_type, value)] = canonical_id
            record = self._data["devices"].setdefault(canonical_id, {})
            record["canonical_device_id"] = canonical_id
            record.setdefault("created_at", time.time())
            record["last_seen"] = max(float(record.get("last_seen", 0.0)), float(device.last_seen))
            self._dirty = True
            if save:
                self._save_locked()
            return canonical_id

    def update_profile(self, canonical_device_id: str, profile: dict[str, Any], *, save: bool = True) -> None:
        with self._lock:
            record = self._data["devices"].setdefault(
                canonical_device_id,
                {
                    "canonical_device_id": canonical_device_id,
                    "created_at": time.time(),
                    "user_name": "",
                },
            )
            record["last_seen"] = float(profile.get("last_seen") or record.get("last_seen") or 0.0)
            record["profile"] = dict(profile)
            self._dirty = True
            if save:
                self._save_locked()

    def set_user_name(self, canonical_device_id: str, name: str) -> bool:
        with self._lock:
            record = self._data["devices"].get(canonical_device_id)
            if not isinstance(record, dict):
                return False
            record["user_name"] = str(name or "").strip()
            self._dirty = True
            self._save_locked()
            return True

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save_locked()

    def flush_if_due(self, interval_s: float = 1.0) -> bool:
        with self._lock:
            if not self._dirty:
                return False
            if time.monotonic() - self._last_saved_at < max(0.0, float(interval_s)):
                return False
            self._save_locked()
            return True

    def stored_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            profiles: list[dict[str, Any]] = []
            for canonical_id, record in self._data["devices"].items():
                profile = dict(record.get("profile") or {})
                if not profile:
                    continue
                profile["canonical_device_id"] = canonical_id
                user_name = str(record.get("user_name") or "").strip()
                if user_name:
                    profile["display_name"] = user_name
                    profile["user_name"] = user_name
                profile.setdefault("online", False)
                profiles.append(profile)
            profiles.sort(key=lambda item: float(item.get("last_seen") or 0.0), reverse=True)
            return profiles

    def compact_mqtt_channels(self) -> int:
        with self._lock:
            removed_count = self._prune_legacy_mqtt_roots_locked()
            repaired_tasmota_count = self._repair_legacy_tasmota_aliases_locked()
            synthetic: list[Device] = []
            for canonical_id, record in self._data["devices"].items():
                profile = record.get("profile") or {}
                identifiers = profile.get("identifiers") or {}
                mqtt_identities = set(identifiers.get("mqtt_identity") or [])
                if not mqtt_identities:
                    continue
                synthetic.append(
                    Device(
                        device_id=canonical_id,
                        total_evidence_count=int(profile.get("evidence_count") or 0),
                        last_seen=float(profile.get("last_seen") or record.get("last_seen") or 0.0),
                        topic_prefixes=set(identifiers.get("mqtt_topic_prefix") or []),
                        mqtt_identities=mqtt_identities,
                    )
                )

            merged_count = 0
            changed = bool(removed_count or repaired_tasmota_count)
            for physical in group_physical_devices(synthetic):
                member_ids = sorted(
                    physical.member_device_ids,
                    key=lambda item: float(self._data["devices"].get(item, {}).get("created_at", 0.0)),
                )
                if not member_ids:
                    continue
                canonical_id = member_ids[0]
                profiles = [
                    dict(self._data["devices"].get(member_id, {}).get("profile") or {})
                    for member_id in member_ids
                ]
                for duplicate_id in member_ids[1:]:
                    self._merge(canonical_id, duplicate_id)
                    merged_count += 1
                combined = _combine_profiles(canonical_id, profiles, physical)
                target = self._data["devices"].setdefault(canonical_id, {})
                if target.get("profile") != combined:
                    target["profile"] = combined
                    changed = True
                target["last_seen"] = float(combined.get("last_seen") or target.get("last_seen") or 0.0)
                for entity_identity in physical.mqtt_entity_identities:
                    self._data["aliases"][_alias_key("mqtt_entity", entity_identity)] = canonical_id

            if merged_count or changed:
                self._dirty = True
                self._save_locked()
            return merged_count + removed_count + repaired_tasmota_count

    def _repair_legacy_tasmota_aliases_locked(self) -> int:
        aliases = self._data["aliases"]
        discovery_ids: set[str] = set()
        changed = 0
        for alias in list(aliases):
            marker = "tasmota/discovery/"
            if marker not in alias:
                continue
            suffix = alias.split(marker, 1)[1].split("/", 1)[0].strip()
            if suffix:
                discovery_ids.add(suffix)
            del aliases[alias]
            changed += 1

        for discovery_id in discovery_ids:
            for alias in list(aliases):
                if not alias.startswith("strong_token:"):
                    continue
                token = alias.split(":", 1)[1]
                if token == discovery_id or token.endswith("|" + discovery_id):
                    del aliases[alias]
                    changed += 1

        remove_devices: set[str] = set()
        for canonical_id, record in self._data["devices"].items():
            profile = record.get("profile") or {}
            identifiers = profile.get("identifiers") or {}
            prefixes = [
                str(value)
                for value in identifiers.get("mqtt_topic_prefix") or []
            ]
            if prefixes and all(
                "tasmota/discovery/" in value.casefold()
                for value in prefixes
            ):
                remove_devices.add(canonical_id)
                continue

            profile_changed = False
            for key in (
                "mqtt_topic_prefix",
                "mqtt_identity",
                "mqtt_entity_prefix",
                "mqtt_entity_identity",
            ):
                values = list(identifiers.get(key) or [])
                filtered = [
                    value
                    for value in values
                    if "tasmota/discovery/" not in str(value).casefold()
                ]
                if filtered != values:
                    identifiers[key] = filtered
                    profile_changed = True
            strong_tokens = list(identifiers.get("strong_token") or [])
            filtered_tokens = [
                value
                for value in strong_tokens
                if str(value).casefold().split("|")[-1] not in discovery_ids
            ]
            if filtered_tokens != strong_tokens:
                identifiers["strong_token"] = filtered_tokens
                profile_changed = True
            if profile_changed:
                profile["identifiers"] = identifiers
                changed += 1

        for canonical_id in remove_devices:
            self._data["devices"].pop(canonical_id, None)
            for alias, owner in list(aliases.items()):
                if owner == canonical_id:
                    del aliases[alias]
            changed += 1
        return changed

    def _prune_legacy_mqtt_roots_locked(self) -> int:
        identities: list[tuple[str, str, str]] = []
        for canonical_id, record in self._data["devices"].items():
            identifiers = (record.get("profile") or {}).get("identifiers") or {}
            for identity in identifiers.get("mqtt_identity") or []:
                source_id, separator, prefix = str(identity).partition("|")
                if separator and prefix:
                    identities.append((source_id, prefix.strip("/"), canonical_id))

        remove_ids: set[str] = set()
        for source_id, prefix, canonical_id in identities:
            if "/" in prefix:
                continue
            descendants = {
                other_id
                for other_source, other_prefix, other_id in identities
                if other_source == source_id
                and other_id != canonical_id
                and other_prefix.casefold().startswith(prefix.casefold() + "/")
            }
            if len(descendants) < 5:
                continue
            record = self._data["devices"].get(canonical_id) or {}
            profile = record.get("profile") or {}
            identifiers = profile.get("identifiers") or {}
            connections = profile.get("connections") or {}
            has_independent_identity = any(
                identifiers.get(key)
                for key in ("hostname", "ssdp_usn", "metadata_identifier")
            ) or any(connections.get(key) for key in ("ip", "mac"))
            if not has_independent_identity:
                remove_ids.add(canonical_id)

        for canonical_id in remove_ids:
            self._data["devices"].pop(canonical_id, None)
            for alias, owner in list(self._data["aliases"].items()):
                if owner == canonical_id:
                    del self._data["aliases"][alias]
        return len(remove_ids)

    def _merge(self, canonical_id: str, duplicate_id: str) -> None:
        if canonical_id == duplicate_id:
            return
        duplicate = self._data["devices"].pop(duplicate_id, {})
        target = self._data["devices"].setdefault(canonical_id, {})
        if float(duplicate.get("last_seen", 0.0)) > float(target.get("last_seen", 0.0)):
            target["last_seen"] = duplicate.get("last_seen", 0.0)
            if duplicate.get("profile"):
                target["profile"] = duplicate["profile"]
        if not target.get("user_name") and duplicate.get("user_name"):
            target["user_name"] = duplicate["user_name"]
        for alias, owner in list(self._data["aliases"].items()):
            if owner == duplicate_id:
                self._data["aliases"][alias] = canonical_id

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("schema_version", 1)
                    payload.setdefault("devices", {})
                    payload.setdefault("aliases", {})
                    return payload
            except Exception:
                pass
        return {"schema_version": 1, "devices": {}, "aliases": {}}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(self._data, indent=2, ensure_ascii=False)
        last_error: Exception | None = None
        for attempt in range(7):
            tmp = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.path)
                self._dirty = False
                self._last_saved_at = time.monotonic()
                return
            except PermissionError as exc:
                last_error = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.025 * (2**attempt))
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        assert last_error is not None
        raise last_error


def _alias_key(alias_type: str, value: str) -> str:
    return f"{alias_type}:{value.strip().lower()}"


def _combine_profiles(canonical_id: str, profiles: list[dict[str, Any]], physical: Device) -> dict[str, Any]:
    profiles = [profile for profile in profiles if profile]
    if not profiles:
        return {"canonical_device_id": canonical_id}
    profiles.sort(key=lambda profile: float(profile.get("last_seen") or 0.0), reverse=True)
    combined = dict(profiles[0])
    combined["canonical_device_id"] = canonical_id
    combined["runtime_device_id"] = physical.device_id
    combined["last_seen"] = max(float(profile.get("last_seen") or 0.0) for profile in profiles)
    combined["evidence_count"] = sum(int(profile.get("evidence_count") or 0) for profile in profiles)
    combined["data"] = {}
    combined["operations"] = []
    identifiers: dict[str, list[str]] = {}
    operations: dict[tuple[str, str, str], dict[str, Any]] = {}
    capabilities: set[str] = set()
    protocols: set[str] = set()
    for profile in profiles:
        combined["data"].update(profile.get("data") or {})
        capabilities.update(str(value) for value in profile.get("capabilities") or [])
        protocols.update(str(value) for value in profile.get("protocols") or [])
        for key, values in (profile.get("identifiers") or {}).items():
            bucket = identifiers.setdefault(key, [])
            for value in values or []:
                if value not in bucket:
                    bucket.append(value)
        for operation in profile.get("operations") or []:
            key = (
                str(operation.get("topic") or ""),
                str(operation.get("action") or ""),
                str(operation.get("sensor_key") or ""),
            )
            operations[key] = operation
    identifiers["mqtt_entity_prefix"] = sorted(physical.mqtt_entity_prefixes)
    identifiers["mqtt_entity_identity"] = sorted(physical.mqtt_entity_identities)
    identifiers["mqtt_channel"] = sorted(physical.mqtt_channels)
    combined["identifiers"] = identifiers
    combined["operations"] = list(operations.values())
    combined["capabilities"] = sorted(capabilities)
    combined["protocols"] = sorted(protocols)
    combined["identity"] = {
        "member_runtime_device_ids": sorted(physical.member_device_ids),
        "observed_topic_count": len(physical.mqtt_identities),
        "entity_count": len(physical.mqtt_entity_identities),
        "channel_count": len(physical.mqtt_channels),
        "reasons": list(physical.identity_reasons),
        "migrated_from_profiles": len(profiles),
    }
    return combined
