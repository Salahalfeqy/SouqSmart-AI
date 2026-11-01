import hmac
from fastapi import Header, HTTPException


def verify_service_key(expected: str, x_service_key: str | None = Header(default=None)) -> None:
    if x_service_key is None or not hmac.compare_digest(x_service_key, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
