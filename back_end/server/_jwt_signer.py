# back_end/server/_jwt_signer.py
from library import *
from ._magnus_config import *


__all__ = [
    "jwt_signer",
]


jwt_signer = JwtSigner(
    secret_key = magnus_config["server"]["jwt_signer"]["secret_key"],
    algorithm = magnus_config["server"]["jwt_signer"]["algorithm"],
    expire_minutes = magnus_config["server"]["jwt_signer"]["expire_minutes"],
)