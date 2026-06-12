"""Annotator interface and registry for source-specific evidence extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent

ANNOTATORS: dict[str, type["Annotator"]] = {}


def register_annotator(source_type: str):
    def wrapper(cls: type[Annotator]) -> type[Annotator]:
        ANNOTATORS[source_type] = cls
        cls.source_type = source_type
        return cls

    return wrapper


class Annotator(ABC):
    source_type: str

    @abstractmethod
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        ...
