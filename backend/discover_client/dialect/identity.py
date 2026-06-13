"""Device identity extraction per MQTT dialect.

Pure function: given a dialect name and topic, extract the logical device id.
"""


def extract_device_id(dialect: str, topic: str) -> str:
    """Extract the device identity from an MQTT topic based on dialect rules.

    Rules per dialect:
    - tasmota:      "cmnd/light-1/Power"        → segments[1] = "light-1"
    - zigbee2mqtt:  "zigbee2mqtt/bulb/set"      → segments[1] = "bulb"
    - subtopic:     "mock/light-1/set/power"    → "/".join(segments[:-2]) = "mock/light-1"
    - flatdict:     "mock/light-1/set"          → "/".join(segments[:-1]) = "mock/light-1"
    - barevalue:    "some/topic"                → segments[0] = "some" (fallback)
    - unknown:      pass-through the entire topic.
    """
    segments = topic.split("/")

    if dialect == "tasmota":
        return segments[1] if len(segments) > 1 else topic
    elif dialect == "zigbee2mqtt":
        return segments[1] if len(segments) > 1 else topic
    elif dialect == "subtopic":
        # Everything except the last 2 segments (action + sensor_key)
        return "/".join(segments[:-2]) if len(segments) > 2 else topic
    elif dialect == "flatdict":
        # Everything except the last segment (action suffix)
        return "/".join(segments[:-1]) if len(segments) > 1 else topic
    elif dialect == "barevalue":
        # First segment as fallback
        return segments[0] if segments else topic
    else:
        # Unknown dialect: pass-through
        return topic
