import re
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core import DomainError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyItem(StrictModel):
    amount: int = Field(strict=True, gt=0, le=1_000_000_000_000)
    kind: Literal["income", "expense"] = "expense"
    category: str = Field(default="Lainnya", min_length=1, max_length=60)
    occurred_on: date
    note: str = Field(default="", max_length=500)

    @field_validator("category")
    @classmethod
    def clean_category(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Kategori kosong")
        return value


class ReminderInput(StrictModel):
    title: str = Field(min_length=1, max_length=250)
    local_date: date
    local_time: str
    mode: Literal["reminder", "alarm"] = "reminder"
    recurrence: Literal["none", "daily", "weekly"] = "none"
    weekdays: list[int] = Field(default_factory=list, max_length=7)

    @field_validator("local_time")
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("Jam harus HH:MM")
        time.fromisoformat(value)
        return value

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, value):
        if any(isinstance(x, bool) or x < 0 or x > 6 for x in value):
            raise ValueError("Hari harus 0 (Senin) sampai 6 (Minggu)")
        return sorted(set(value))


class Proposal(StrictModel):
    kind: Literal[
        "chat",
        "clarify",
        "reminder.create",
        "reminder.list",
        "reminder.cancel",
        "finance.draft",
        "finance.report",
        "finance.history",
        "memory.remember",
    ]
    text: str = Field(default="", max_length=6000)
    reminder: ReminderInput | None = None
    items: list[MoneyItem] = Field(default_factory=list, max_length=5)
    resource_id: str | None = Field(default=None, max_length=32)
    period: str | None = Field(default=None, max_length=10)
    fact: str | None = Field(default=None, max_length=800)


def parse_amount(raw: str) -> int:
    value = raw.lower().strip().removeprefix("rp").strip()
    match = re.fullmatch(r"([0-9.,]+)\s*(ribu|rb|k|juta|jt)?", value)
    if not match:
        raise DomainError("Nominal tidak valid. Contoh: 25000, 25.000, atau 25 ribu.")
    digits, suffix = match.groups()
    if suffix:
        # Decimal with a suffix is explicit: 25,5 ribu = 25.500; dot must be thousands.
        if "." in digits:
            if not re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", digits):
                raise DomainError("Nominal ambigu. Gunakan 25000 atau 25,5 ribu.")
            digits = digits.replace(".", "")
        multiplier = 1_000_000 if suffix in {"juta", "jt"} else 1000
        try:
            amount = Decimal(digits.replace(",", ".")) * multiplier
        except InvalidOperation:
            raise DomainError("Nominal tidak valid.") from None
        if amount != amount.to_integral_value():
            raise DomainError("Nominal harus berupa rupiah bulat.")
        result = int(amount)
    else:
        if "," in digits or ("." in digits and not re.fullmatch(r"\d{1,3}(\.\d{3})+", digits)):
            raise DomainError("Nominal ambigu. Gunakan bilangan rupiah bulat seperti 25000.")
        result = int(digits.replace(".", ""))
    if not 0 < result <= 1_000_000_000_000:
        raise DomainError("Nominal harus lebih dari 0 dan maksimal 1 triliun rupiah.")
    return result
