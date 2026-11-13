"""Published research formulations accompanying the executable task catalog."""

from functools import cache
from importlib.resources import files

from submission_api.schemas_public import FormulationList


@cache
def published_formulations() -> FormulationList:
    return FormulationList.model_validate_json(
        files("submission_api").joinpath("formulations.json").read_text(encoding="utf-8")
    )
