# back_end/server/schemas/_blueprint.py
"""Blueprint creation / response / params / preference schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from ._user import UserInfo


class BlueprintCreate(BaseModel):
    id: str
    title: str
    description: str
    code: str


class BlueprintResponse(BaseModel):
    id: str
    title: str
    description: str
    code: str
    user_id: str
    updated_at: datetime
    user: Optional[UserInfo] = None
    can_manage: bool = False
    class Config: from_attributes = True


class PagedBlueprintResponse(BaseModel):
    total: int
    items: List[BlueprintResponse]


class BlueprintParamOption(BaseModel):
    label: str
    value: Any
    description: Optional[str] = None


class BlueprintParamSchema(BaseModel):
    key: str
    label: str
    type: str
    default: Any = None
    description: Optional[str] = None
    scope: Optional[str] = None
    allow_empty: bool = True
    is_optional: bool = False
    is_list: bool = False
    is_item_optional: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    placeholder: Optional[str] = None
    multi_line: bool = False
    min_lines: Optional[int] = None
    color: Optional[str] = None
    border_color: Optional[str] = None
    options: Optional[List[BlueprintParamOption]] = None


class BlueprintPreferenceUpdate(BaseModel):
    blueprint_id: str
    blueprint_hash: str
    cached_params: Dict[str, Any]


class BlueprintPreferenceResponse(BaseModel):
    blueprint_id: str
    blueprint_hash: str
    cached_params: Dict[str, Any]
    updated_at: datetime
