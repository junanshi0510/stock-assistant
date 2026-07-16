# -*- coding: utf-8 -*-
"""Idempotently provision the dedicated private OSS bucket."""

from __future__ import annotations

import json
from typing import Any

from object_storage import AliyunObjectStorage


def _service_error_code(error: Exception) -> tuple[int | None, str]:
    current = error
    seen: set[int] = set()
    while callable(getattr(current, "unwrap", None)) and id(current) not in seen:
        seen.add(id(current))
        unwrapped = current.unwrap()
        if not isinstance(unwrapped, Exception) or unwrapped is current:
            break
        current = unwrapped
    status = getattr(current, "status_code", None)
    code = str(getattr(current, "code", "") or "")
    try:
        return int(status) if status is not None else None, code
    except (TypeError, ValueError):
        return None, code


def provision_bucket(storage: AliyunObjectStorage | None = None) -> dict[str, Any]:
    storage = storage or AliyunObjectStorage()
    oss = storage._oss
    client = storage._client
    created = False
    try:
        client.get_bucket_info(oss.GetBucketInfoRequest(bucket=storage.bucket))
    except (oss.exceptions.ServiceError, oss.exceptions.OperationError) as error:
        status, code = _service_error_code(error)
        if status != 404 and code not in {"NoSuchBucket", "NoSuchBucketInfo"}:
            raise
        client.put_bucket(
            oss.PutBucketRequest(
                bucket=storage.bucket,
                acl="private",
                create_bucket_configuration=oss.CreateBucketConfiguration(
                    storage_class="Standard",
                    data_redundancy_type="LRS",
                ),
            )
        )
        created = True

    client.put_bucket_acl(
        oss.PutBucketAclRequest(bucket=storage.bucket, acl="private")
    )
    client.put_bucket_public_access_block(
        oss.PutBucketPublicAccessBlockRequest(
            bucket=storage.bucket,
            public_access_block_configuration=(
                oss.models.bucket_public_access_block.PublicAccessBlockConfiguration(
                    block_public_access=True
                )
            ),
        )
    )
    rules = [
        oss.LifecycleRule(
            id="expire-holding-ocr",
            prefix="private/holding-ocr/",
            status="Enabled",
            expiration=oss.LifecycleRuleExpiration(days=2),
        ),
        oss.LifecycleRule(
            id="expire-postgresql-backups",
            prefix="backups/postgresql/",
            status="Enabled",
            expiration=oss.LifecycleRuleExpiration(days=180),
        ),
        oss.LifecycleRule(
            id="abort-incomplete-multipart",
            prefix="",
            status="Enabled",
            abort_multipart_upload=oss.LifecycleRuleAbortMultipartUpload(days=7),
        ),
    ]
    client.put_bucket_lifecycle(
        oss.PutBucketLifecycleRequest(
            bucket=storage.bucket,
            lifecycle_configuration=oss.LifecycleConfiguration(rules=rules),
        )
    )

    acl_result = client.get_bucket_acl(
        oss.GetBucketAclRequest(bucket=storage.bucket)
    )
    public_result = client.get_bucket_public_access_block(
        oss.GetBucketPublicAccessBlockRequest(bucket=storage.bucket)
    )
    lifecycle_result = client.get_bucket_lifecycle(
        oss.GetBucketLifecycleRequest(bucket=storage.bucket)
    )
    acl = str(getattr(acl_result, "acl", "") or "")
    public_configuration = getattr(
        public_result, "public_access_block_configuration", None
    )
    blocked = bool(getattr(public_configuration, "block_public_access", False))
    actual_rules = list(
        getattr(
            getattr(lifecycle_result, "lifecycle_configuration", None),
            "rules",
            [],
        )
        or []
    )
    rule_ids = sorted(str(getattr(rule, "id", "") or "") for rule in actual_rules)
    expected_ids = sorted(str(rule.id) for rule in rules)
    if acl != "private":
        raise RuntimeError(f"OSS bucket ACL verification failed: {acl or 'missing'}")
    if not blocked:
        raise RuntimeError("OSS bucket public-access block verification failed")
    if rule_ids != expected_ids:
        raise RuntimeError("OSS bucket lifecycle verification failed")
    readiness = storage.readiness()
    if not readiness.get("ready"):
        raise RuntimeError("OSS bucket readiness verification failed")
    return {
        "status": "ready",
        "provider": "aliyun_oss",
        "bucket": storage.bucket,
        "region": storage.settings.region,
        "created": created,
        "acl": acl,
        "public_access_blocked": blocked,
        "lifecycle_rules": rule_ids,
        "encryption": storage.encryption_mode,
    }


def main() -> int:
    print(
        json.dumps(
            provision_bucket(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
