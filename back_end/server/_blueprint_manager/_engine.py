"""Python Blueprint semantics, imported only inside the evaluator sandbox."""

import inspect
import math
from enum import Enum
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import TypeAdapter, ValidationError

from .._blueprint_protocol import BlueprintParamOption, BlueprintParamSchema
from ._sandbox import _compile_code
from ._types import (
    FileSecret,
    _BlueprintCapture,
    _is_list_type,
    _is_optional_type,
    _unwrap_list,
    _unwrap_optional,
)


class Parameter:
    def __init__(
        self,
        parameter: inspect.Parameter,
        annotation: Any,
    ) -> None:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise ValueError(
                f"Unsupported parameter kind for '{parameter.name}'",
            )
        self.name = parameter.name
        self.default = parameter.default
        self.annotation = annotation
        self.metadata: dict[str, Any] = {}
        if (
            get_origin(
                annotation,
            )
            is Annotated
        ):
            annotation, *extras = get_args(
                annotation,
            )
            for extra in extras:
                if isinstance(
                    extra,
                    dict,
                ):
                    self.metadata.update(
                        extra,
                    )
        self.optional = _is_optional_type(
            annotation,
        )
        annotation = _unwrap_optional(
            annotation,
        )
        self.is_list = _is_list_type(
            annotation,
        )
        annotation = _unwrap_list(
            annotation,
        )
        self.item_optional = self.is_list and _is_optional_type(
            annotation,
        )
        self.base = _unwrap_optional(
            annotation,
        )
        self.is_file = isinstance(
            self.base,
            type,
        ) and issubclass(
            self.base,
            FileSecret,
        )
        if (
            self.base not in (Any, str, int, float, bool, FileSecret)
            and not self.is_file
            and get_origin(
                self.base,
            )
            is not Literal
        ):
            raise ValueError(
                f"Unsupported type for parameter '{self.name}'",
            )
        self.literals = (
            get_args(
                self.base,
            )
            if get_origin(
                self.base,
            )
            is Literal
            else ()
        )
        if self.literals and any(
            type(
                item,
            )
            not in (str, int, float, bool)
            for item in self.literals
        ):
            raise ValueError(
                f"Unsupported Literal value for parameter '{self.name}'",
            )
        self.adapter = TypeAdapter(
            self.base,
        )
        self.schema = self._schema()
        if self.default is not inspect.Parameter.empty:
            self.validate(
                self.default,
            )

    def _schema(
        self,
    ) -> BlueprintParamSchema:
        metadata = self.metadata
        kind = {
            int: "number",
            float: "float",
            bool: "boolean",
            FileSecret: "file_secret",
        }.get(
            self.base,
            "text",
        )
        if self.literals:
            kind = "select"
        elif self.is_file:
            kind = "file_secret"
        schema = BlueprintParamSchema(
            key=self.name,
            label=metadata.get(
                "label",
                self.name.replace(
                    "_",
                    " ",
                ).title(),
            ),
            type=kind,
            default=None if self.default is inspect.Parameter.empty else self.default,
            is_optional=self.optional,
            is_list=self.is_list,
            is_item_optional=self.item_optional,
        )
        for key in (
            "description",
            "scope",
            "placeholder",
            "color",
            "border_color",
            "multi_line",
            "min_lines",
        ):
            if key in metadata:
                setattr(
                    schema,
                    key,
                    metadata[key],
                )
        if "allow_empty" in metadata:
            if not isinstance(
                metadata["allow_empty"],
                bool,
            ):
                raise ValueError(
                    f"allow_empty must be boolean for '{self.name}'",
                )
            schema.allow_empty = metadata["allow_empty"]
        if self.is_file:
            schema.allow_empty = False
        for key in ("min", "max"):
            if key in metadata:
                value = metadata[key]
                if type(
                    value,
                ) not in (int, float) or not math.isfinite(
                    value,
                ):
                    raise ValueError(
                        f"{key} must be finite for '{self.name}'",
                    )
                setattr(
                    schema,
                    key,
                    value,
                )
        if (
            schema.min is not None
            and schema.max is not None
            and schema.min > schema.max
        ):
            raise ValueError(
                f"min exceeds max for '{self.name}'",
            )
        if self.literals:
            options = metadata.get(
                "options",
                {},
            )
            if not isinstance(
                options,
                dict,
            ):
                raise ValueError(
                    f"options must be a mapping for '{self.name}'",
                )
            schema.options = []
            for value in self.literals:
                detail = options.get(
                    value,
                    {},
                )
                if isinstance(
                    detail,
                    str,
                ):
                    detail = {"label": detail}
                if not isinstance(
                    detail,
                    dict,
                ):
                    raise ValueError(
                        f"Invalid option metadata for '{self.name}'",
                    )
                schema.options.append(
                    BlueprintParamOption(
                        value=value,
                        label=detail.get(
                            "label",
                            str(
                                value,
                            ),
                        ),
                        description=detail.get(
                            "description",
                        ),
                    ),
                )
        return BlueprintParamSchema.model_validate(
            schema.model_dump(),
        )

    def _validate_item(
        self,
        value: Any,
    ) -> Any:
        if (
            self.literals
            and isinstance(
                value,
                str,
            )
            and value not in self.literals
        ):
            matches = [
                item
                for item in self.literals
                if str(
                    item,
                )
                == value
            ]
            if (
                len(
                    matches,
                )
                == 1
            ):
                value = matches[0]
        value = self.adapter.validate_python(
            value,
        )
        if isinstance(
            value,
            float,
        ) and not math.isfinite(
            value,
        ):
            raise ValueError(
                "value must be finite",
            )
        if self.base in (int, float):
            if self.schema.min is not None and value < self.schema.min:
                raise ValueError(
                    f"value must be >= {self.schema.min}",
                )
            if self.schema.max is not None and value > self.schema.max:
                raise ValueError(
                    f"value must be <= {self.schema.max}",
                )
        if (
            isinstance(
                value,
                str,
            )
            and not self.schema.allow_empty
            and not value.strip()
        ):
            raise ValueError(
                "value must not be empty",
            )
        return value

    def validate(
        self,
        value: Any,
    ) -> Any:
        try:
            if value is None and self.optional:
                return None
            if self.is_list:
                if value is None:
                    raise ValueError(
                        "list must not be null",
                    )
                values = (
                    value
                    if isinstance(
                        value,
                        list,
                    )
                    else [value]
                )
                return [
                    None
                    if item is None and self.item_optional
                    else self._validate_item(
                        item,
                    )
                    for item in values
                ]
            return self._validate_item(
                value,
            )
        except (ValueError, TypeError, ValidationError) as error:
            raise ValueError(
                f"Parameter '{self.name}': {error}",
            ) from None


class BlueprintEvaluator:
    def __init__(
        self,
        code: str,
        job_types: dict[str, str],
    ) -> None:
        def capture(
            **kwargs: Any,
        ) -> None:
            raise _BlueprintCapture(
                kwargs,
            )

        self.job_type = Enum(
            "JobType",
            job_types,
            type=str,
        )
        scope = _compile_code(
            code,
            {
                "submit_job": capture,
                "JobType": self.job_type,
                "FileSecret": FileSecret,
                "Annotated": Annotated,
                "Literal": Literal,
                "Optional": Optional,
                "List": List,
                "Dict": Dict,
                "Any": Any,
            },
        )
        self.function = scope["blueprint"]
        if (
            not inspect.isfunction(
                self.function,
            )
            or inspect.iscoroutinefunction(
                self.function,
            )
            or inspect.isgeneratorfunction(
                self.function,
            )
        ):
            raise ValueError(
                "Blueprint must define a synchronous, non-generator function",
            )
        signature = inspect.signature(
            self.function,
        )
        annotations = get_type_hints(
            self.function,
            include_extras=True,
        )
        self.parameters = [
            Parameter(
                parameter,
                annotations.get(
                    name,
                    Any,
                ),
            )
            for name, parameter in signature.parameters.items()
        ]

    def schema(
        self,
    ) -> list[dict[str, Any]]:
        return [
            parameter.schema.model_dump(
                mode="json",
            )
            for parameter in self.parameters
        ]

    def execute(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        expected = {parameter.name for parameter in self.parameters}
        unknown = (
            set(
                inputs,
            )
            - expected
        )
        if unknown:
            raise ValueError(
                f"Unknown parameter(s): {', '.join(sorted(unknown))}",
            )
        arguments = {}
        for parameter in self.parameters:
            value = inputs.get(
                parameter.name,
                parameter.default,
            )
            if value is inspect.Parameter.empty:
                raise ValueError(
                    f"Missing required parameter '{parameter.name}'",
                )
            arguments[parameter.name] = parameter.validate(
                value,
            )
        try:
            self.function(
                **arguments,
            )
        except _BlueprintCapture as capture:
            payload = capture.payload
            if isinstance(
                payload.get(
                    "job_type",
                ),
                self.job_type,
            ):
                payload["job_type"] = payload["job_type"].value
            return payload
        raise ValueError(
            "Blueprint function must call submit_job()",
        )
