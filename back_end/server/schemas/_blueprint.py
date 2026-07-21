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


class BlueprintListItem(BaseModel):
    """列表视图的轻量投影：省掉可达几十 MB 的 code 列（列表从不渲染它，编辑 / 详情
    走 GET /blueprints/{id} 按需取）。与 job 侧 JobListItem 同型，见其说明。"""
    id: str
    title: str
    description: str
    user_id: str
    updated_at: datetime
    user: Optional[UserInfo] = None
    can_manage: bool = False
    class Config: from_attributes = True


class BlueprintResponse(BlueprintListItem):
    """完整视图（详情 / 提交返回），在轻量投影之上补齐 code，只由单条 blueprint 的
    endpoint 返回，绝不进列表。"""
    code: str


class PagedBlueprintResponse(BaseModel):
    total: int
    items: List[BlueprintListItem]


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
