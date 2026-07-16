# -*- coding: utf-8 -*-
"""Upload a verified PostgreSQL backup and checksum to private OSS."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from object_storage import AliyunObjectStorage


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_backup(path: Path, expected_sha256: str | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = _sha256_file(path)
    if expected_sha256 and digest != expected_sha256.strip().lower():
        raise RuntimeError("备份文件 SHA-256 与本地清单不一致")
    now = dt.datetime.now(dt.timezone.utc)
    key = f"backups/postgresql/{now:%Y/%m}/{path.name}"
    storage = AliyunObjectStorage()
    result = storage.put_file(
        key,
        path,
        content_type="application/vnd.postgresql.custom-backup",
        metadata={
            "sha256": digest,
            "backup-kind": "postgresql-custom",
            "created-at": now.isoformat(timespec="seconds"),
        },
    )
    checksum_key = key + ".sha256"
    storage.put_bytes(
        checksum_key,
        f"{digest}  {path.name}\n".encode("ascii"),
        content_type="text/plain",
        metadata={"backup-object": key},
    )
    return {
        "provider": "aliyun_oss",
        "bucket": storage.bucket,
        "object_key": key,
        "checksum_key": checksum_key,
        "sha256": digest,
        "byte_size": result["content_length"],
        "encryption": result["server_side_encryption"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    print(
        json.dumps(
            upload_backup(args.path, args.sha256),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

