from dataclasses import dataclass, field


@dataclass(slots=True)
class ScannerConfig:
    mode: str = "passive"
    interface: str = ""
    scan_interval: float = 1.0
    filter_bssids: set[str] = field(default_factory=set)
