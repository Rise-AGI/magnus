# back_end/server/schemas/__init__.py
"""Pydantic API schemas, organized by domain.

每个域文件定义自己的请求/响应模型；跨域引用（如 ServiceResponse → JobListItem → UserInfo）
通过域间 import 串起来。所有 schema 都从本包 re-export。

- _user.py:       UserInfo, UserDetail, AgentCreate, HeadcountUpdate, PagedUserResponse, TransferRequest
- _auth.py:       FeishuLoginRequest, TokenLoginRequest, LoginResponse, TokenResponse
- _job.py:        JobSubmission, JobListItem, JobResponse, PagedJobResponse
- _cluster.py:    ClusterResources, ClusterStatsResponse
- _blueprint.py:  Blueprint*/PagedBlueprint*/BlueprintParam*/BlueprintPreference*
- _service.py:    ServiceCreate, ServiceResponse, PagedServiceResponse
- _explorer.py:   ExplorerSession*/ExplorerMessage*/PagedExplorerSessionResponse
- _skill.py:      Skill*/PagedSkillResponse
- _image.py:      CachedImageCreate, CachedImageResponse, PagedCachedImageResponse
- _chat.py:       Conversation*/Message*/PagedConversationResponse/PagedMessageResponse/AddMemberRequest/ConversationUpdate
"""
from ._user import (
    UserInfo,
    TransferRequest,
    UserDetail,
    AgentCreate,
    HeadcountUpdate,
    PagedUserResponse,
)
from ._auth import (
    FeishuLoginRequest,
    TokenLoginRequest,
    LoginResponse,
    TokenResponse,
)
from ._job import (
    JobSubmission,
    JobListItem,
    JobResponse,
    PagedJobResponse,
)
from ._cluster import (
    ClusterResources,
    ClusterStatsResponse,
)
from ._blueprint import (
    BlueprintCreate,
    BlueprintResponse,
    PagedBlueprintResponse,
    BlueprintParamOption,
    BlueprintParamSchema,
    BlueprintPreferenceUpdate,
    BlueprintPreferenceResponse,
)
from ._service import (
    ServiceCreate,
    ServiceResponse,
    PagedServiceResponse,
)
from ._explorer import (
    ExplorerMessageCreate,
    ExplorerMessageResponse,
    ExplorerSessionCreate,
    ExplorerSessionOwner,
    ExplorerSessionResponse,
    ExplorerSessionWithMessages,
    PagedExplorerSessionResponse,
)
from ._skill import (
    SkillFileCreate,
    SkillFileResponse,
    SkillCreate,
    SkillResponse,
    PagedSkillResponse,
)
from ._image import (
    CachedImageCreate,
    CachedImageResponse,
    PagedCachedImageResponse,
)
from ._chat import (
    ConversationCreate,
    ConversationMemberResponse,
    MessageResponse,
    ConversationResponse,
    ConversationListItem,
    PagedConversationResponse,
    MessageCreate,
    PagedMessageResponse,
    AddMemberRequest,
    ConversationUpdate,
)


__all__ = [
    "JobSubmission",
    "JobListItem",
    "JobResponse",
    "PagedJobResponse",
    "FeishuLoginRequest",
    "TokenLoginRequest",
    "UserInfo",
    "UserDetail",
    "AgentCreate",
    "PagedUserResponse",
    "LoginResponse",
    "ClusterStatsResponse",
    "BlueprintCreate",
    "BlueprintResponse",
    "PagedBlueprintResponse",
    "BlueprintParamOption",
    "BlueprintParamSchema",
    "ServiceCreate",
    "ServiceResponse",
    "PagedServiceResponse",
    "BlueprintPreferenceUpdate",
    "BlueprintPreferenceResponse",
    "ExplorerMessageCreate",
    "ExplorerMessageResponse",
    "ExplorerSessionCreate",
    "ExplorerSessionResponse",
    "ExplorerSessionWithMessages",
    "PagedExplorerSessionResponse",
    "SkillFileCreate",
    "SkillFileResponse",
    "SkillCreate",
    "SkillResponse",
    "PagedSkillResponse",
    "CachedImageCreate",
    "CachedImageResponse",
    "PagedCachedImageResponse",
    "ConversationCreate",
    "ConversationResponse",
    "ConversationListItem",
    "PagedConversationResponse",
    "ConversationMemberResponse",
    "MessageCreate",
    "MessageResponse",
    "PagedMessageResponse",
    "AddMemberRequest",
    "ConversationUpdate",
]
