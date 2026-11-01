"""JSON boundary shared by the controller and the isolated Python evaluator."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class BlueprintParamOption(BaseModel):
    label: str
    value: Any
    description: str | None = None


class BlueprintParamSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    type: str
    default: Any = None
    description: str | None = None
    scope: str | None = None
    allow_empty: bool = True
    is_optional: bool = False
    is_list: bool = False
    is_item_optional: bool = False
    min: float | None = None
    max: float | None = None
    placeholder: str | None = None
    multi_line: bool = False
    min_lines: int | None = None
    color: str | None = None
    border_color: str | None = None
    options: list[BlueprintParamOption] | None = None
