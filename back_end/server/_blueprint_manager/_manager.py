"""Controller-side validation of data returned by an isolated evaluator."""

from typing import Any

from ..models import JobType
from ..schemas import BlueprintParamSchema, JobSubmission
from ._runner import BlueprintRuntime, RuntimeSettings


class BlueprintManager:
    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        owner_key: str | None = None,
    ) -> None:
        self.runtime = BlueprintRuntime(
            settings,
            owner_key=owner_key,
        )

    def _evaluate(
        self,
        code: str,
        operation: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.runtime.evaluate(
            {
                "code": code,
                "operation": operation,
                "parameters": parameters or {},
                "job_types": {
                    name: value.value for name, value in JobType.__members__.items()
                },
            },
        )

    def analyze_signature(
        self,
        code: str,
    ) -> list[BlueprintParamSchema]:
        response = self._evaluate(
            code,
            "schema",
        )
        schema = response.get(
            "schema",
        )
        if not isinstance(
            schema,
            list,
        ):
            raise ValueError(
                "Blueprint evaluator returned an invalid schema",
            )
        return [
            BlueprintParamSchema.model_validate(
                item,
            )
            for item in schema
        ]

    def execute(
        self,
        code: str,
        inputs: dict[str, Any],
    ) -> JobSubmission:
        response = self._evaluate(
            code,
            "execute",
            inputs,
        )
        payload = response.get(
            "job",
        )
        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "Blueprint evaluator returned an invalid Job",
            )
        unknown = (
            set(
                payload,
            )
            - JobSubmission.model_fields.keys()
        )
        if unknown:
            raise ValueError(
                f"Unknown submit_job field(s): {', '.join(sorted(unknown))}",
            )
        return JobSubmission.model_validate(
            payload,
        )
