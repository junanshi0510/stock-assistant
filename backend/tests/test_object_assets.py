from __future__ import annotations

import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

from PIL import Image

from image_uploads import normalize_ocr_image
from object_assets import ObjectAssetRepository
from object_storage import (
    AliyunObjectStorage,
    ObjectStorageConfigurationError,
    ObjectStorageSettings,
)
from provision_object_storage import provision_bucket


class ObjectAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "objects.db"
        self.repository = ObjectAssetRepository(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_image_is_decoded_resized_and_metadata_is_stripped(self):
        source = Image.new("RGB", (9000, 20), "white")
        buffer = BytesIO()
        source.save(buffer, format="JPEG", exif=b"Exif\x00\x00test-metadata")
        normalized, info = normalize_ocr_image(buffer.getvalue())
        self.assertLessEqual(max(info["width"], info["height"]), 4096)
        self.assertTrue(info["metadata_stripped"])
        with Image.open(BytesIO(normalized)) as parsed:
            self.assertFalse(parsed.getexif())

    def test_oversized_pixel_canvas_is_rejected_before_full_decode(self):
        source = Image.new("1", (7100, 7100), 1)
        buffer = BytesIO()
        source.save(buffer, format="PNG", optimize=True)
        with self.assertRaisesRegex(ValueError, "总像素过大"):
            normalize_ocr_image(buffer.getvalue())

    def test_asset_lifecycle_is_audited_and_events_are_immutable(self):
        asset, created = self.repository.reserve(
            user_id="user-a",
            purpose="holding_ocr",
            bucket="private-bucket",
            object_key="private/holding-ocr/object.jpg",
            sha256="a" * 64,
            content_type="image/jpeg",
            byte_size=120,
            retention_until="2026-07-17T01:00:00+00:00",
            encryption_mode="AES256",
            metadata={"metadata_stripped": True},
        )
        self.assertTrue(created)
        self.assertEqual(self.repository.mark_available(asset["id"])["status"], "available")
        self.repository.mark_deleted(asset["id"])
        self.assertEqual(self.repository.get(asset["id"])["status"], "deleted")
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE object_asset_events SET event_type='tampered' WHERE asset_id=?",
                    (asset["id"],),
                )
        finally:
            connection.close()

    def test_private_object_key_never_contains_user_identifier(self):
        settings = ObjectStorageSettings(
            region="cn-hangzhou",
            bucket="private-stock-assistant",
            endpoint=None,
            access_key_id="test-id",
            access_key_secret="test-secret",
            security_token=None,
            key_pepper="p" * 64,
            encryption_mode="AES256",
            kms_key_id=None,
            use_internal_endpoint=False,
        )
        storage = AliyunObjectStorage(settings)
        key = storage.build_private_key(
            "user-sensitive-identifier", "holding_ocr", "jpg"
        )
        self.assertNotIn("user-sensitive-identifier", key)
        self.assertTrue(key.startswith("private/holding-ocr/"))
        self.assertTrue(key.endswith(".jpg"))

    def test_missing_object_storage_configuration_has_no_local_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ObjectStorageConfigurationError):
                ObjectStorageSettings.from_environment()

    def test_bucket_provisioning_enforces_private_access_and_lifecycle(self):
        settings = ObjectStorageSettings(
            region="cn-hangzhou",
            bucket="private-stock-assistant",
            endpoint=None,
            access_key_id="test-id",
            access_key_secret="test-secret",
            security_token=None,
            key_pepper="p" * 64,
            encryption_mode="AES256",
            kms_key_id=None,
            use_internal_endpoint=False,
        )
        storage = AliyunObjectStorage(settings)
        client = Mock()
        lifecycle_state = {}

        def save_lifecycle(request):
            lifecycle_state["configuration"] = request.lifecycle_configuration

        client.put_bucket_lifecycle.side_effect = save_lifecycle
        client.get_bucket_acl.return_value = SimpleNamespace(acl="private")
        client.get_bucket_public_access_block.return_value = SimpleNamespace(
            public_access_block_configuration=SimpleNamespace(
                block_public_access=True
            )
        )
        client.get_bucket_lifecycle.side_effect = lambda _request: SimpleNamespace(
            lifecycle_configuration=lifecycle_state["configuration"]
        )
        storage._client = client
        result = provision_bucket(storage)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["created"])
        self.assertEqual(result["acl"], "private")
        self.assertTrue(result["public_access_blocked"])
        self.assertEqual(len(result["lifecycle_rules"]), 3)
        client.put_bucket.assert_not_called()
        client.put_bucket_acl.assert_called_once()
        client.put_bucket_public_access_block.assert_called_once()


if __name__ == "__main__":
    unittest.main()
