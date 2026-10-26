from pathlib import Path

from ...core.datafile import read_json
from .event import WeightedEvent
from .property import Property


class AgeManager:
    def __init__(self, prop: Property):
        self.prop = prop
        self.ages: dict[int, list[WeightedEvent]] = {}

    def load(self, path: Path):
        # 优先读 <path>.gz（包里只发布压缩版，见 core/datafile.py）
        data: dict[str, dict] = read_json(path, {})
        self.ages = {
            int(k): [WeightedEvent(s) for s in v.get("event", [])]
            for k, v in data.items()
        }

    def get_events(self) -> list[WeightedEvent]:
        return self.ages[self.prop.AGE]

    def grow(self):
        self.prop.AGE += 1
