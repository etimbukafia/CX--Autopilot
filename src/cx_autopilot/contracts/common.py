"""Shared validation and immutable model primitives."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue


class FrozenMap(Mapping[str, Any]):
    """Small read-only mapping used inside immutable records."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = {key: freeze_value(value) for key, value in values.items()}

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return repr(self._values)


def freeze_value(value: Any) -> Any:
    """Recursively freeze standard mutable containers."""

    if isinstance(value, FrozenMap):
        return value
    if isinstance(value, Mapping):
        return FrozenMap(value)
    if isinstance(value, list):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_value(item) for item in value)
    return value


class ImmutableModel(BaseModel):
    """Base for provider-neutral, immutable, strict records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    def model_post_init(self, __context: Any) -> None:
        """Freeze nested mappings and containers after Pydantic validation."""

        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            frozen = freeze_value(value)
            if frozen is not value:
                object.__setattr__(self, field_name, frozen)

    def model_dump_json(
        self,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        **kwargs: Any,
    ) -> str:
        """Serialize immutable mappings through the standard JSON boundary."""

        kwargs.pop("warnings", None)
        kwargs.pop("fallback", None)
        kwargs["warnings"] = False
        value = to_jsonable(super().model_dump(mode="python", **kwargs))
        separators = None if indent is not None else (",", ":")
        return json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, separators=separators)


def aware_timestamp(value: datetime, field_name: str) -> datetime:
    """Require a timezone-aware timestamp without changing its value."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def non_blank(value: str, field_name: str) -> str:
    """Reject empty, whitespace-only, and surrounding-whitespace identities."""

    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def unique_values(values: Sequence[Any], field_name: str) -> None:
    """Require a sequence to contain unique values."""

    try:
        duplicate = len(values) != len(set(values))
    except TypeError as exc:
        raise ValueError(f"{field_name} must contain hashable values") from exc
    if duplicate:
        raise ValueError(f"{field_name} must not contain duplicates")


def unique_refs(values: Sequence[Any], field_name: str) -> None:
    """Require exact-reference-like values to have unique identities."""

    identities = [getattr(value, "identity", value) for value in values]
    unique_values(identities, field_name)


def validate_mapping(value: Mapping[str, JsonValue], field_name: str) -> dict[str, JsonValue]:
    """Copy a JSON mapping and reject blank keys."""

    copied = dict(value)
    if any(not key.strip() for key in copied):
        raise ValueError(f"{field_name} keys must not be blank")
    return copied


ModelT = TypeVar("ModelT", bound=BaseModel)


def to_jsonable(value: Any) -> Any:
    """Convert a model tree into deterministic JSON-compatible values."""

    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    return value
