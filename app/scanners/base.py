from abc import ABC, abstractmethod

from app.schemas import RawOpportunity


class BaseScanner(ABC):
    name: str

    @abstractmethod
    def scan(self) -> list[RawOpportunity]:
        """Collect opportunities from one source."""
        raise NotImplementedError