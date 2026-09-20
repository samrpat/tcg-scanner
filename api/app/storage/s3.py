"""S3 / MinIO backend. Phase 10.

Present so the interface has a second implementation to hold it honest, and so the
configuration switch exists from day one.
"""

from typing import BinaryIO

from app.storage.base import StoredObject

_NOT_YET = (
    "The S3 storage backend lands in Phase 10. Set STORAGE_BACKEND=local for now."
)


class S3Storage:
    def __init__(self, **_: object) -> None:
        raise NotImplementedError(_NOT_YET)

    def put(self, path: str, data: bytes | BinaryIO, *, overwrite: bool = False) -> StoredObject:
        raise NotImplementedError(_NOT_YET)

    def get(self, path: str) -> bytes:
        raise NotImplementedError(_NOT_YET)

    def exists(self, path: str) -> bool:
        raise NotImplementedError(_NOT_YET)

    def delete(self, path: str) -> None:
        raise NotImplementedError(_NOT_YET)

    def url(self, path: str) -> str:
        raise NotImplementedError(_NOT_YET)
