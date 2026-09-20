"""Storage interface. Swapping the backend must require no change in calling code."""

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    path: str
    bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None


class Storage(Protocol):
    def put(self, path: str, data: bytes | BinaryIO, *, overwrite: bool = False) -> StoredObject:
        """Write an object. Refuses to clobber unless `overwrite` is set."""
        ...

    def get(self, path: str) -> bytes: ...

    def exists(self, path: str) -> bool: ...

    def delete(self, path: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under a prefix. Returns how many objects went.

        Needed because SKUs are reused: allocation is `max(sku) + 1`, so after the last card is
        deleted the next scan is `CARD-000001` again and would write into a directory still
        holding the previous card's photographs.
        """
        ...

    def url(self, path: str) -> str:
        """A URL the web layer can serve this from."""
        ...
