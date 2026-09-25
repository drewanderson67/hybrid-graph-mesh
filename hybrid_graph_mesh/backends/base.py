from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from ..ir import Query
from ..model import ResultSet

Kind = Literal["rdf", "lpg"]


class Backend(ABC):
    name: str
    kind: Kind

    @abstractmethod
    def labels(self) -> set[str]: ...

    @abstractmethod
    def relationships(self) -> set[str]: ...

    @abstractmethod
    def properties(self) -> set[str]: ...

    @abstractmethod
    def compile(self, q: Query) -> str:
        """Render the IR into this backend's native query language."""

    @abstractmethod
    def execute(self, q: Query) -> ResultSet: ...

    @abstractmethod
    def execute_native(self, text: str) -> ResultSet:
        """Run a raw query written in this backend's native language."""

    def identity_property(self) -> str:
        return "name"

    def covers(self, q: Query) -> bool:
        return q.labels() <= self.labels() and q.rel_types() <= self.relationships()
