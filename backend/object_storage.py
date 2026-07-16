# -*- coding: utf-8 -*-
"""Private Alibaba Cloud OSS adapter. There is intentionally no local fallback."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ObjectStorageConfigurationError(RuntimeError):
    pass


class ObjectStorageIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectStorageSettings:
    region: str
    bucket: str
    endpoint: str | None
    access_key_id: str
    access_key_secret: str
    security_token: str | None
    key_pepper: str
    encryption_mode: str
    kms_key_id: str | None
    use_internal_endpoint: bool

    @classmethod
    def from_environment(cls) -> "ObjectStorageSettings":
        region = str(os.getenv("OSS_REGION") or "").strip()
        bucket = str(os.getenv("OSS_BUCKET") or "").strip()
        access_key_id = str(
            os.getenv("OSS_ACCESS_KEY_ID")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
            or ""
        ).strip()
        access_key_secret = str(
            os.getenv("OSS_ACCESS_KEY_SECRET")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
            or ""
        ).strip()
        key_pepper = str(os.getenv("OBJECT_KEY_PEPPER") or "").strip()
        missing = [
            name
            for name, value in (
                ("OSS_REGION", region),
                ("OSS_BUCKET", bucket),
                ("OSS_ACCESS_KEY_ID", access_key_id),
                ("OSS_ACCESS_KEY_SECRET", access_key_secret),
                ("OBJECT_KEY_PEPPER", key_pepper if len(key_pepper) >= 32 else ""),
            )
            if not value
        ]
        if missing:
            raise ObjectStorageConfigurationError(
                "对象存储配置不完整: " + ", ".join(missing)
            )
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", bucket):
            raise ObjectStorageConfigurationError("OSS_BUCKET 格式无效")
        encryption_mode = str(os.getenv("OSS_SSE_MODE") or "AES256").strip()
        if encryption_mode not in {"AES256", "KMS"}:
            raise ObjectStorageConfigurationError("OSS_SSE_MODE 只能是 AES256 或 KMS")
        kms_key_id = str(os.getenv("OSS_KMS_KEY_ID") or "").strip() or None
        if encryption_mode == "KMS" and not kms_key_id:
            raise ObjectStorageConfigurationError("KMS 加密必须配置 OSS_KMS_KEY_ID")
        return cls(
            region=region,
            bucket=bucket,
            endpoint=str(os.getenv("OSS_ENDPOINT") or "").strip() or None,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=str(os.getenv("OSS_SECURITY_TOKEN") or "").strip() or None,
            key_pepper=key_pepper,
            encryption_mode=encryption_mode,
            kms_key_id=kms_key_id,
            use_internal_endpoint=str(os.getenv("OSS_USE_INTERNAL_ENDPOINT") or "1")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
        )


class AliyunObjectStorage:
    def __init__(self, settings: ObjectStorageSettings | None = None) -> None:
        self.settings = settings or ObjectStorageSettings.from_environment()
        try:
            import alibabacloud_oss_v2 as oss
        except ImportError as error:
            raise ObjectStorageConfigurationError(
                "缺少 alibabacloud-oss-v2 依赖"
            ) from error
        credentials = oss.credentials.StaticCredentialsProvider(
            self.settings.access_key_id,
            self.settings.access_key_secret,
            self.settings.security_token,
        )
        config = oss.Config(
            region=self.settings.region,
            endpoint=self.settings.endpoint,
            credentials_provider=credentials,
            connect_timeout=5,
            readwrite_timeout=30,
            retry_max_attempts=3,
            use_internal_endpoint=(
                self.settings.use_internal_endpoint and self.settings.endpoint is None
            ),
        )
        self._oss = oss
        self._client = oss.Client(config)

    @property
    def bucket(self) -> str:
        return self.settings.bucket

    @property
    def encryption_mode(self) -> str:
        return self.settings.encryption_mode

    def build_private_key(self, user_id: str, purpose: str, suffix: str) -> str:
        safe_purpose = re.sub(r"[^a-z0-9-]+", "-", purpose.lower()).strip("-")
        if not safe_purpose:
            raise ValueError("invalid object purpose")
        user_hash = hmac.new(
            self.settings.key_pepper.encode("utf-8"),
            str(user_id).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        now = dt.datetime.now(dt.timezone.utc)
        extension = re.sub(r"[^a-z0-9]", "", suffix.lower())[:8] or "bin"
        return (
            f"private/{safe_purpose}/{now:%Y/%m/%d}/{user_hash}/"
            f"{uuid.uuid4().hex}.{extension}"
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request = self._oss.PutObjectRequest(
            bucket=self.bucket,
            key=key,
            body=data,
            content_length=len(data),
            content_type=content_type,
            metadata=metadata or {},
            server_side_encryption=self.settings.encryption_mode,
            server_side_encryption_key_id=self.settings.kms_key_id,
            forbid_overwrite=True,
        )
        result = self._client.put_object(request)
        head = self.head(key)
        if int(head.get("content_length") or -1) != len(data):
            raise ObjectStorageIntegrityError("OSS 上传后长度校验失败")
        if str(head.get("server_side_encryption") or "") != self.settings.encryption_mode:
            raise ObjectStorageIntegrityError("OSS 服务端加密校验失败")
        return {
            "etag": str(getattr(result, "etag", "") or ""),
            "content_length": len(data),
            "server_side_encryption": head["server_side_encryption"],
        }

    def put_file(
        self,
        key: str,
        path: str | os.PathLike[str],
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        source = Path(path)
        size = source.stat().st_size
        with source.open("rb") as body:
            result = self._client.put_object(
                self._oss.PutObjectRequest(
                    bucket=self.bucket,
                    key=key,
                    body=body,
                    content_length=size,
                    content_type=content_type,
                    metadata=metadata or {},
                    server_side_encryption=self.settings.encryption_mode,
                    server_side_encryption_key_id=self.settings.kms_key_id,
                    forbid_overwrite=True,
                )
            )
        head = self.head(key)
        if int(head.get("content_length") or -1) != size:
            raise ObjectStorageIntegrityError("OSS 文件上传后长度校验失败")
        if str(head.get("server_side_encryption") or "") != self.settings.encryption_mode:
            raise ObjectStorageIntegrityError("OSS 文件服务端加密校验失败")
        return {
            "etag": str(getattr(result, "etag", "") or ""),
            "content_length": size,
            "server_side_encryption": head["server_side_encryption"],
        }

    def head(self, key: str) -> dict[str, Any]:
        result = self._client.head_object(
            self._oss.HeadObjectRequest(bucket=self.bucket, key=key)
        )
        return {
            "content_length": int(getattr(result, "content_length", 0) or 0),
            "content_type": str(getattr(result, "content_type", "") or ""),
            "metadata": dict(getattr(result, "metadata", {}) or {}),
            "server_side_encryption": str(
                getattr(result, "server_side_encryption", "") or ""
            ),
        }

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        result = self._client.get_object(
            self._oss.GetObjectRequest(bucket=self.bucket, key=key)
        )
        length = int(getattr(result, "content_length", 0) or 0)
        if length <= 0 or length > int(max_bytes):
            if getattr(result, "body", None):
                result.body.close()
            raise ObjectStorageIntegrityError("OSS 对象长度超出任务限制")
        try:
            data = result.body.read()
        finally:
            result.body.close()
        if len(data) != length:
            raise ObjectStorageIntegrityError("OSS 下载长度校验失败")
        return data

    def delete(self, key: str) -> None:
        self._client.delete_object(
            self._oss.DeleteObjectRequest(bucket=self.bucket, key=key)
        )

    def readiness(self) -> dict[str, Any]:
        try:
            self._client.get_bucket_info(
                self._oss.GetBucketInfoRequest(bucket=self.bucket)
            )
            return {
                "ready": True,
                "provider": "aliyun_oss",
                "bucket": self.bucket,
                "region": self.settings.region,
                "encryption": self.settings.encryption_mode,
            }
        except Exception as error:
            return {
                "ready": False,
                "provider": "aliyun_oss",
                "bucket": self.bucket,
                "region": self.settings.region,
                "error": type(error).__name__,
            }
