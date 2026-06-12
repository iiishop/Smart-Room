"""MQTT annotator that extracts identification clues from MQTT events."""

from __future__ import annotations

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent


@register_annotator("mqtt")
class MqttAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "data":
            return None

        topic = event.payload.get("topic", "")
        payload_value = event.payload.get("value", {})
        payload_keys = set(payload_value.keys()) if isinstance(payload_value, dict) else set()

        hostname_hint = None
        parts = topic.split("/")
        if len(parts) >= 2 and parts[1]:
            hostname_hint = parts[1]

        return SignalEvidence(
            source_id=event.source_id,
            source_type="mqtt",
            mqtt_topic=topic,
            mqtt_payload_keys=payload_keys,
            hostname=hostname_hint,
            timestamp=event.timestamp,
        )
