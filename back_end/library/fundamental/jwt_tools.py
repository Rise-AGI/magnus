import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


__all__ = [
    "JwtSigner",
]


class JwtSigner:
    
    def __init__(
        self, 
        secret_key: str, 
        algorithm: str = "HS256", 
        expire_minutes: int = 10080
    ):
        
        """
        初始化 JWT 签名器
        :param secret_key: 密钥 (由上层传入)
        :param algorithm: 加密算法，默认 HS256
        :param expire_minutes: 默认过期时间 (分钟)，默认 7 天
        """
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.expire_minutes = expire_minutes


    def create_access_token(
        self, 
        payload: Dict[str, Any], 
        expires_delta: Optional[timedelta] = None
    )-> str:
        
        """
        生成 Token
        """
        to_encode = payload.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.expire_minutes)
        
        # 添加标准过期字段
        to_encode.update({"exp": expire})
        
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    
    def decode_access_token(
        self, 
        token: str
    )-> Optional[Dict[str, Any]]:
        
        """
        验证并解析 Token
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.PyJWTError:
            # 无论是过期还是签名错误，都视为无效
            return None