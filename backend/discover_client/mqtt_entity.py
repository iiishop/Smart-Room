"""Infer physical MQTT entities separately from their channels/properties."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from typing import Iterable

from discover_client.identification.device import Device


@dataclass(frozen=True)
class MqttIdentityResolution:
    source_id: str
    observed_prefix: str
    entity_prefix: str
    channel: str
    confidence: float
    reason: str

    @property
    def entity_identity(self) -> str:
        return f"{self.source_id}|{self.entity_prefix}"


class MqttEntityModel:
    """Learn repeated channel positions from the full observed MQTT topic tree.

    A terminal segment is treated as a channel only when the same segment occurs
    below multiple distinct parents. This groups repeated schemas such as
    location/{LMS,TPS,CDS} without maintaining a vocabulary of sensor names.
    """

    def __init__(self, devices: Iterable[Device], min_distinct_parents: int = 2) -> None:
        self.min_distinct_parents = max(2, int(min_distinct_parents))
        self._leaf_parents: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._parent_children: dict[tuple[str, str], set[str]] = defaultdict(set)
        for device in devices:
            for identity in device.mqtt_identities:
                source_id, prefix = split_mqtt_identity(identity)
                parts = split_topic(prefix)
                if len(parts) < 2:
                    continue
                parent = "/".join(parts[:-1])
                leaf = parts[-1]
                self._leaf_parents[(source_id, leaf.casefold())].add(parent.casefold())
                self._parent_children[(source_id, parent.casefold())].add(leaf.casefold())

    def resolve(self, identity: str) -> MqttIdentityResolution:
        source_id, prefix = split_mqtt_identity(identity)
        parts = split_topic(prefix)
        if len(parts) < 2:
            return MqttIdentityResolution(source_id, prefix, prefix, "", 1.0, "single-level MQTT identity")

        parent = "/".join(parts[:-1])
        leaf = parts[-1]
        distinct_parents = len(self._leaf_parents.get((source_id, leaf.casefold()), set()))
        sibling_count = len(self._parent_children.get((source_id, parent.casefold()), set()))
        if distinct_parents >= self.min_distinct_parents:
            confidence = min(0.98, 0.72 + 0.04 * distinct_parents + 0.02 * max(0, sibling_count - 1))
            return MqttIdentityResolution(
                source_id=source_id,
                observed_prefix=prefix,
                entity_prefix=parent,
                channel=leaf,
                confidence=confidence,
                reason=(
                    f"terminal segment appears as a channel under {distinct_parents} distinct "
                    f"entity parents; this parent currently exposes {sibling_count} channel(s)"
                ),
            )
        return MqttIdentityResolution(
            source_id=source_id,
            observed_prefix=prefix,
            entity_prefix=prefix,
            channel="",
            confidence=0.65,
            reason="topic prefix remains the entity because no repeated channel structure was observed",
        )


def group_physical_devices(devices: list[Device]) -> list[Device]:
    model = MqttEntityModel(devices)
    groups: dict[str, Device] = {}
    for device in devices:
        resolutions = [model.resolve(identity) for identity in sorted(device.mqtt_identities)]
        entity_ids = sorted({resolution.entity_identity for resolution in resolutions})
        if len(entity_ids) == 1:
            group_key = "mqtt:" + entity_ids[0].casefold()
        else:
            group_key = "runtime:" + device.device_id

        target = groups.get(group_key)
        if target is None:
            digest = hashlib.sha1(group_key.encode("utf-8")).hexdigest()[:12]
            target = Device(device_id=f"physical-{digest}")
            groups[group_key] = target
        _merge_device(target, device)
        for resolution in resolutions:
            target.mqtt_entity_prefixes.add(resolution.entity_prefix)
            target.mqtt_entity_identities.add(resolution.entity_identity)
            if resolution.channel:
                target.mqtt_channels.add(resolution.channel)
            reason = (
                f"{resolution.observed_prefix} -> entity {resolution.entity_prefix}"
                + (f", channel {resolution.channel}" if resolution.channel else "")
                + f" ({resolution.reason}, confidence {resolution.confidence:.2f})"
            )
            if reason not in target.identity_reasons:
                target.identity_reasons.append(reason)

    result = list(groups.values())
    result.sort(key=lambda item: item.last_seen, reverse=True)
    return result


def split_mqtt_identity(identity: str) -> tuple[str, str]:
    source_id, separator, prefix = str(identity or "").partition("|")
    if not separator:
        return "", source_id.strip("/")
    return source_id, prefix.strip("/")


def split_topic(topic: str) -> list[str]:
    return [segment.strip() for segment in str(topic or "").split("/") if segment.strip()]


def _merge_device(target: Device, source: Device) -> None:
    target.total_evidence_count += source.total_evidence_count
    target.last_seen = max(target.last_seen, source.last_seen)
    target.ip_addresses.update(source.ip_addresses)
    target.hostnames.update(source.hostnames)
    target.mac_addresses.update(source.mac_addresses)
    target.mac_prefixes.update(source.mac_prefixes)
    target.service_types.update(source.service_types)
    target.ssdp_usns.update(source.ssdp_usns)
    target.payload_keys.update(source.payload_keys)
    target.topic_prefixes.update(source.topic_prefixes)
    target.mqtt_identities.update(source.mqtt_identities)
    target.mqtt_client_ids.update(source.mqtt_client_ids)
    target.source_ids.update(source.source_ids)
    target.identity_tokens.update(source.identity_tokens)
    for reason in source.identity_reasons:
        if reason not in target.identity_reasons:
            target.identity_reasons.append(reason)
    target.member_device_ids.add(source.device_id)
    target.member_device_ids.update(source.member_device_ids)
    target.explicit_names.update(source.explicit_names)
    target.explicit_manufacturers.update(source.explicit_manufacturers)
    target.explicit_models.update(source.explicit_models)
    target.explicit_descriptions.update(source.explicit_descriptions)
    target.metadata_capabilities.update(source.metadata_capabilities)
    target.metadata_identifiers.update(source.metadata_identifiers)
    target.metadata_sources.update(source.metadata_sources)
    target.mqtt_command_topics.update(source.mqtt_command_topics)
    for topic, values in source.mqtt_command_values.items():
        target.mqtt_command_values.setdefault(topic, set()).update(values)
    if target.vendor is None and source.vendor:
        target.vendor = source.vendor
    if target.os_guess is None and source.os_guess:
        target.os_guess = source.os_guess
