"""MQTT annotator that extracts identification clues from MQTT events."""

from __future__ import annotations

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent
from discover_client.identification.tokens import extract_identity_tokens


@register_annotator("mqtt")
class MqttAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "data":
            return None

        topic = event.payload.get("topic", "")
        payload_value = event.payload.get("value", {})
        payload_keys = set(payload_value.keys()) if isinstance(payload_value, dict) else set()

        # Extract topic_prefix: govee/H5179/abc/temperature → govee/H5179/abc
        topic_prefix = None
        parts = topic.rsplit("/", 1)
        if len(parts) == 2 and parts[0]:
            topic_prefix = parts[0]

        return SignalEvidence(
            source_id=event.source_id,
            source_type="mqtt",
            mqtt_topic=topic,
            mqtt_payload_keys=payload_keys,
            mqtt_payload=payload_value,
            topic_prefix=topic_prefix,
            identity_tokens=extract_identity_tokens(topic, payload_value),
            timestamp=event.timestamp,
        )
