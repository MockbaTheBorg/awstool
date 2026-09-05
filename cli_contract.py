"""Generic CLI module contract shared by the host and plug-in modules."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


class CommandError(Exception):
    """Base exception for command and host failures."""

    def __init__(self, message: str, *, show_usage: bool = False, exit_code: int = 1) -> None:
        super().__init__(message)
        self.show_usage = show_usage
        self.exit_code = exit_code


@dataclass(frozen=True)
class OptionSpec:
    flags: tuple[str, ...]
    help: str
    dest: str | None = None
    action: str | None = None
    choices: tuple[str, ...] | None = None
    default: Any = None
    metavar: str | None = None
    required: bool = False

    def argparse_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"help": self.help}
        if self.dest:
            kwargs["dest"] = self.dest
        if self.action:
            kwargs["action"] = self.action
        if self.choices:
            kwargs["choices"] = self.choices
        if self.default is not None:
            kwargs["default"] = self.default
        if self.metavar:
            kwargs["metavar"] = self.metavar
        return kwargs


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    action_flags: tuple[str, str]
    help: str
    options: tuple[OptionSpec, ...] = ()
    usage_examples: tuple[str, ...] = ()


@dataclass
class ResultEnvelope:
    command: str
    source: str | None
    status: str
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def command_result(
    command: str,
    source: str | None,
    data: Any = None,
    *,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    status: str = "ok",
) -> ResultEnvelope:
    return ResultEnvelope(
        command=command,
        source=source,
        status=status,
        data=data,
        warnings=warnings or [],
        errors=errors or [],
    )


def dataclass_to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: dataclass_to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_plain(item) for item in value]
    return value


def to_json(value: Any) -> str:
    return json.dumps(dataclass_to_plain(value), indent=2, sort_keys=True)
