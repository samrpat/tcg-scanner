"""Storage factory."""

from functools import lru_cache

from app.config import settings
from app.storage.base import StoredObject
from app.storage.local import LocalStorage
from app.storage.paths import (
    UnsafePathError,
    format_sku,
    image_path,
    is_original,
    item_dir,
    parse_sku,
    validate_sku,
)
from app.storage.s3 import S3Storage

__all__ = [
    "LocalStorage",
    "S3Storage",
    "StoredObject",
    "UnsafePathError",
    "format_sku",
    "get_storage",
    "image_path",
    "is_original",
    "item_dir",
    "parse_sku",
    "validate_sku",
]


@lru_cache
def get_storage():
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage(settings.storage_local_root)
    if backend == "s3":
        return S3Storage()
    raise ValueError(f"unknown STORAGE_BACKEND: {settings.storage_backend!r}")
