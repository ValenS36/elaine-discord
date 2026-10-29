from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


def uid() -> str:
    return uuid4().hex


class Clock(Protocol):
    def now(self) -> int: ...


class SystemClock:
    def now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())


class DomainError(Exception):
    """Safe Indonesian message that may be shown to the user."""


@dataclass(frozen=True)
class Actor:
    workspace: str
    user: str
    membership: str
    channel: str
    timezone: str
    private: bool = False
    admin: bool = False


@dataclass
class Result:
    text: str
    kind: str = "chat"
    data: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False


def rupiah(value: int) -> str:
    return ("-" if value < 0 else "") + "Rp" + f"{abs(value):,}".replace(",", ".")
