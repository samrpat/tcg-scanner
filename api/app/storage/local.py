"""Local filesystem backend."""

import hashlib
import io
import os
from pathlib import Path
from typing import BinaryIO

from PIL import Image as PILImage

from app.storage.base import StoredObject
from app.storage.paths import UnsafePathError


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        """Resolve inside the root, or refuse.

        Belt and braces alongside `validate_sku`: even a path that got past the SKU check
        cannot land outside the storage root.
        """
        if path.startswith("/") or "\x00" in path:
            raise UnsafePathError(f"absolute or invalid path: {path!r}")
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise UnsafePathError(f"path escapes storage root: {path!r}")
        return candidate

    def put(self, path: str, data: bytes | BinaryIO, *, overwrite: bool = False) -> StoredObject:
        target = self._resolve(path)
        if target.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite {path}")
        payload = data if isinstance(data, bytes) else data.read()
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to a temporary file and rename, so an interrupted write never leaves a
        # half-written image that later looks valid.
        tmp = target.with_suffix(target.suffix + ".partial")
        tmp.write_bytes(payload)
        os.replace(tmp, target)

        width = height = None
        try:
            with PILImage.open(io.BytesIO(payload)) as img:
                width, height = img.size
        except Exception:  # noqa: BLE001 - non-images are stored, just not measured
            pass

        return StoredObject(
            path=path,
            bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            width=width,
            height=height,
        )

    def get(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.exists():
            target.unlink()

    def delete_prefix(self, prefix: str) -> int:
        """Delete a whole item directory. See `Storage.delete_prefix`.

        Goes through `_resolve`, which is the traversal guard — a prefix is as
        user-influenced as a path, and this one removes a directory tree.
        """
        target = self._resolve(prefix)
        if not target.exists() or not target.is_dir():
            return 0
        removed = 0
        for child in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_file():
                child.unlink()
                removed += 1
            elif child.is_dir():
                child.rmdir()
        target.rmdir()
        return removed

    def url(self, path: str) -> str:
        return f"/api/images/{path}"

    def writable(self) -> bool:
        """Used by the readiness check."""
        probe = self.root / ".write-probe"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False
