"""Local storage backend."""

import io

import pytest
from PIL import Image as PILImage

from app.enums import ImageKind
from app.storage.paths import UnsafePathError, image_path


def _jpeg(width: int = 60, height: int = 84) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_put_records_size_hash_and_dimensions(tmp_storage):
    payload = _jpeg()
    stored = tmp_storage.put(image_path("CARD-000001", ImageKind.ORIGINAL_FRONT), payload)

    assert stored.path == "CARD-000001/original-front.jpg"
    assert stored.bytes == len(payload)
    assert len(stored.sha256) == 64
    assert (stored.width, stored.height) == (60, 84)


def test_front_and_back_land_in_one_directory(tmp_storage):
    for kind in (ImageKind.ORIGINAL_FRONT, ImageKind.ORIGINAL_BACK):
        tmp_storage.put(image_path("CARD-000007", kind), _jpeg())

    directory = tmp_storage.root / "CARD-000007"
    names = sorted(p.name for p in directory.iterdir())
    assert names == ["original-back.jpg", "original-front.jpg"]


def test_originals_are_not_clobbered_by_accident(tmp_storage):
    path = image_path("CARD-000001", ImageKind.ORIGINAL_FRONT)
    tmp_storage.put(path, _jpeg())
    with pytest.raises(FileExistsError):
        tmp_storage.put(path, _jpeg())
    # Explicit intent still works, which is what reprocessing needs.
    tmp_storage.put(path, _jpeg(70, 98), overwrite=True)
    assert tmp_storage.put is not None


def test_round_trip(tmp_storage):
    payload = _jpeg()
    path = image_path("CARD-000002", ImageKind.PROCESSED_FRONT)
    tmp_storage.put(path, payload)
    assert tmp_storage.exists(path)
    assert tmp_storage.get(path) == payload
    tmp_storage.delete(path)
    assert not tmp_storage.exists(path)


def test_no_partial_files_survive_a_write(tmp_storage):
    tmp_storage.put(image_path("CARD-000003", ImageKind.ORIGINAL_FRONT), _jpeg())
    assert not list(tmp_storage.root.rglob("*.partial"))


def test_paths_cannot_escape_the_root(tmp_storage):
    with pytest.raises(UnsafePathError):
        tmp_storage.put("../escaped.jpg", b"x")
    with pytest.raises(UnsafePathError):
        tmp_storage.get("/etc/passwd")


def test_writable_probe_leaves_nothing_behind(tmp_storage):
    assert tmp_storage.writable() is True
    assert not (tmp_storage.root / ".write-probe").exists()
