from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from wifi_positioning.models import RssiReading


class RssiCollector(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def collect(self) -> AsyncIterator[RssiReading]:
        raise NotImplementedError

    @abstractmethod
    async def list_aps(self) -> list[str]:
        raise NotImplementedError
