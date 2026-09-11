"""Typed boundaries for untrusted model output, before touching stored plans."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

Prose = Annotated[StrictStr, Field(min_length=1, max_length=4000)]


class BriefPayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    readiness_summary: Prose
    training_prescription: Prose
    rationale: Annotated[StrictStr, Field(max_length=4000)] | None = None
    flags: dict[Annotated[str, Field(max_length=64)], StrictBool] | None = Field(default=None, max_length=20)
    readiness_score: float | None = None
    session_planned: StrictBool | None = None
    session_items: object = None


class AdjustPayload(BaseModel):
    reply: Prose
    changed: StrictBool = False
    training_prescription: Annotated[StrictStr, Field(max_length=4000)] | None = None
    session_items: object = None


class HeadlinePayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    readiness_summary: Annotated[StrictStr, Field(max_length=4000)] = ""
    readiness_score: float | None = None
