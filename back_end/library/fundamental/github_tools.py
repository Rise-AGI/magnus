from .typing import *
from .externals import *

__all__ = [
    "GitHubClient",
]

class GitHubClient:
    def __init__(self, token: Optional[str] = None):
        self.base_url = "https://api.github.com/repos"
        
        # 组装 Headers
        headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        
        # ✅ 如果有 Token，注入鉴权头
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # 💡 如果你在国内服务器，httpx 会自动读取系统代理
        self.client = httpx.AsyncClient(
            headers = headers,
            timeout = 10.0,
            follow_redirects = True,
        )

    async def fetch_branches(self, namespace: str, repo: str)-> List[Dict[str, Any]]:
        """获取分支列表"""
        url = f"{self.base_url}/{namespace}/{repo}/branches"
        print(f"[GithubTool] Fetching: {url}")
        
        resp = await self.client.get(url)
        if resp.status_code != 200:
            # 404 可能代表仓库不存在，也可能代表 Token 没权限看不到
            print(f"❌ GitHub API Error: {resp.status_code} - {resp.text}")
            return []
            
        data = resp.json()
        return [{"name": item["name"], "commit_sha": item["commit"]["sha"]} for item in data]

    async def fetch_commits(self, namespace: str, repo: str, branch_sha: str)-> List[Dict[str, Any]]:
        """获取某分支下的 Commits"""
        url = f"{self.base_url}/{namespace}/{repo}/commits"
        params = {"sha": branch_sha, "per_page": 10} 
        
        resp = await self.client.get(url, params=params)
        if resp.status_code != 200:
            print(f"❌ GitHub API Error: {resp.status_code} - {resp.text}")
            return []

        data = resp.json()
        results = []
        for item in data:
            c = item["commit"]
            results.append({
                "sha": item["sha"],
                "message": c["message"].split('\n')[0],
                "author": c["author"]["name"],
                "date": c["author"]["date"]
            })
        return results

    async def close(self):
        await self.client.aclose()