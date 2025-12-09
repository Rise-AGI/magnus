# back_end/library/functional/feishu_tools.py
from ..fundamental import *

__all__ = [
    "FeishuClient",
]

class FeishuClient:
    
    def __init__(
        self, 
        app_id: str, 
        app_secret: str, 
        redirect_uri: str
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.host = "https://open.feishu.cn"

    async def _get_tenant_access_token(self, client: httpx.AsyncClient) -> str:
        """
        Step 1: 获取应用维度的凭证 (Tenant Access Token)
        这是调用飞书后续接口的“入场券”。
        """
        url = f"{self.host}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }

        # 🔍 DEBUG: Step 1
        print(f"📡 [Feishu Step 1] Getting App Token...")

        try:
            resp = await client.post(url, json=payload)
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(f"Step 1 Failed (App Token): {data}")
            
            return data["tenant_access_token"]
        
        except Exception as e:
            print(f"❌ [Feishu Step 1] Error: {e}")
            raise e

    async def get_feishu_user(self, code: str) -> Dict[str, Any]:
        """
        标准三步走流程：Code -> User Info
        """
        async with httpx.AsyncClient() as client:
            
            # -------------------------------------------------------
            # 1. 先拿 App Token (Tenant Access Token)
            # -------------------------------------------------------
            app_token = await self._get_tenant_access_token(client)
            
            # -------------------------------------------------------
            # 2. 用 App Token + Code -> 换 User Access Token
            # -------------------------------------------------------
            url_step2 = f"{self.host}/open-apis/authen/v1/access_token"
            headers_app = {
                "Authorization": f"Bearer {app_token}",  # 👈 关键！之前缺了这个
                "Content-Type": "application/json; charset=utf-8"
            }
            payload_step2 = {
                "grant_type": "authorization_code",
                "code": code
            }

            print(f"📡 [Feishu Step 2] Exchanging Code for User Token...")
            resp_step2 = await client.post(url_step2, json=payload_step2, headers=headers_app)
            data_step2 = resp_step2.json()

            # 🔍 DEBUG
            if data_step2.get("code") != 0:
                print(f"📩 [Feishu Step 2] Response: {data_step2}")
                raise RuntimeError(f"Step 2 Failed (User Token): {data_step2.get('msg')}")

            user_access_token = data_step2["data"]["access_token"]

            # -------------------------------------------------------
            # 3. 用 User Access Token -> 换 User Info
            # -------------------------------------------------------
            url_step3 = f"{self.host}/open-apis/authen/v1/user_info"
            headers_user = {
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            print(f"📡 [Feishu Step 3] Fetching User Profile...")
            resp_step3 = await client.get(url_step3, headers=headers_user)
            data_step3 = resp_step3.json()

            # 🔍 DEBUG
            print(f"📩 [Feishu Step 3] Response: {data_step3}")

            if data_step3.get("code") != 0:
                raise RuntimeError(f"Step 3 Failed (User Info): {data_step3.get('msg')}")

            # 成功！返回用户信息
            # 包含: name, avatar_url, open_id, union_id, en_name 等
            return data_step3["data"]