from __future__ import annotations

import os
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    prefix: str = "bit"

    @classmethod
    def from_env(cls) -> "R2Config":
        required = {
            "R2_ACCOUNT_ID": os.getenv("R2_ACCOUNT_ID"),
            "R2_ACCESS_KEY_ID": os.getenv("R2_ACCESS_KEY_ID"),
            "R2_SECRET_ACCESS_KEY": os.getenv("R2_SECRET_ACCESS_KEY"),
            "R2_BUCKET": os.getenv("R2_BUCKET"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required R2 environment variables: {', '.join(missing)}")
        return cls(
            account_id=required["R2_ACCOUNT_ID"] or "",
            access_key_id=required["R2_ACCESS_KEY_ID"] or "",
            secret_access_key=required["R2_SECRET_ACCESS_KEY"] or "",
            bucket=required["R2_BUCKET"] or "",
            prefix=(os.getenv("R2_PREFIX") or "bit").strip("/"),
        )


class R2Storage:
    def __init__(self, config: R2Config) -> None:
        self.config = config
        endpoint_url = f"https://{config.account_id}.r2.cloudflarestorage.com"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name="auto",
        )

    def normalized_key(self, key: str) -> str:
        return "/".join(part.strip("/") for part in [self.config.prefix, key] if part)

    def exists(self, key: str) -> bool:
        normalized_key = self.normalized_key(key)
        try:
            self.client.head_object(Bucket=self.config.bucket, Key=normalized_key)
            return True
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = error.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def put_bytes(self, key: str, body: bytes, content_type: str) -> str:
        normalized_key = self.normalized_key(key)
        self.client.put_object(
            Bucket=self.config.bucket,
            Key=normalized_key,
            Body=body,
            ContentType=content_type,
        )
        return normalized_key
