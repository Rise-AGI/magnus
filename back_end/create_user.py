#!/usr/bin/env python3
"""
创建 Magnus 用户并返回 token。

用法：
    cd back_end && uv run python create_user.py [用户名]
    
示例：
    uv run python create_user.py test_user
    uv run python create_user.py  # 默认用户名 "admin"
"""
import sys
import secrets
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy.orm import Session
from server.database import SessionLocal, engine, Base
from server.models import User
from server._magnus_config import magnus_config


def generate_trust_token() -> str:
    """生成 sk-xxx 格式的 trust token"""
    return f"sk-{secrets.token_urlsafe(24)}"


def create_user(name: str = "admin") -> tuple[User, str]:
    """
    创建用户或返回已有用户。
    
    Returns:
        (User, token) - 用户对象和 trust token
    """
    db: Session = SessionLocal()
    try:
        # 检查是否已存在同名用户
        existing = db.query(User).filter(User.name == name).first()
        if existing:
            if existing.token:
                return existing, existing.token
            # 没有 token，生成一个
            existing.token = generate_trust_token()
            db.commit()
            db.refresh(existing)
            return existing, existing.token
        
        # 创建新用户
        token = generate_trust_token()
        new_user = User(
            name=name,
            token=token,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return new_user, token
    finally:
        db.close()


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "admin"
    user, token = create_user(name)
    
    print(f"用户 ID:  {user.id}")
    print(f"用户名:   {user.name}")
    print(f"Token:    {token}")
    print()
    print("使用方式:")
    print(f"  magnus login local -a http://127.0.0.1:8017 -t {token}")
    print()
    print("或设置环境变量:")
    print(f"  export MAGNUS_ADDRESS=http://127.0.0.1:8017")
    print(f"  export MAGNUS_TOKEN={token}")


if __name__ == "__main__":
    main()