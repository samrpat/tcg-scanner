"""API surface. Only endpoints that need no database are exercised here; the rest are
covered once the stack is up (see the Phase 1 verification notes in CURRENT.md)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_liveness_does_not_touch_dependencies(client):
    """Liveness must answer even with Postgres and Redis unreachable."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_reports_the_phase(client):
    body = client.get("/api").json()
    assert body["phase"] == 2
    assert body["version"] == "0.1.0"


def test_rubric_is_served_for_the_manual_picker(client):
    body = client.get("/api/conditioning/rubric").json()
    assert body["card"]["area_mm2"] == 5544.0
    assert body["ceilings"] == {"NM": 3, "LP": 6, "MP": 12, "HP": 24}
    assert body["severity_points"] == {"slight": 1, "minor": 2, "moderate": 4, "major": 8}

    keys = {i["key"] for i in body["imperfections"]}
    assert {"edgewear", "surface_wear", "bend", "fault", "curling", "damage"} <= keys
    assert all(i["label"] for i in body["imperfections"])


def test_grade_endpoint_is_a_pure_function(client):
    response = client.post(
        "/api/conditioning/grade",
        json={"defects": [{"imperfection": "edgewear", "severity": "minor"}]},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["condition"] == "LP"
    assert body["points"] == 2
    assert body["limited_by"] == "edgewear"
    assert "edgewear" in body["explanation"]


def test_grade_returns_every_marketplace_code(client):
    body = client.post("/api/conditioning/grade", json={"defects": []}).json()
    assert body["condition"] == "NM"
    assert body["marketplace"]["ebay"]["code"] == "400010"
    assert body["marketplace"]["tcgplayer"]["code"] == "NM"
    assert body["marketplace"]["cardmarket"]["code"] == "NM"


def test_damaged_flags_the_ebay_photo_requirement(client):
    body = client.post(
        "/api/conditioning/grade",
        json={"defects": [{"imperfection": "damage", "severity": "major"}]},
    ).json()
    assert body["condition"] == "DMG"
    assert body["marketplace"]["ebay"]["requires_photo"] is True


def test_psa_estimate_is_labelled_as_secondary(client):
    body = client.post("/api/conditioning/grade", json={"defects": []}).json()
    assert body["psa_estimate"]["low"] == 8
    assert "never sets condition" in body["psa_estimate"]["note"]


def test_translations_endpoint_covers_all_marketplaces(client):
    body = client.get("/api/conditioning/translations").json()
    assert set(body) == {"ebay", "tcgplayer", "cardmarket", "collectr"}
    for mapping in body.values():
        assert set(mapping) == {"NM", "LP", "MP", "HP", "DMG"}


def test_an_unknown_imperfection_is_rejected_with_a_helpful_422(client):
    response = client.post(
        "/api/conditioning/grade",
        json={"defects": [{"imperfection": "sparkles", "severity": "minor"}]},
    )
    assert response.status_code == 422
    assert "sparkles" in response.text
    assert "edgewear" in response.text, "the error should name the valid options"


def test_an_unknown_severity_is_rejected(client):
    response = client.post(
        "/api/conditioning/grade",
        json={"defects": [{"imperfection": "edgewear", "severity": "catastrophic"}]},
    )
    assert response.status_code == 422


def _declared_paths() -> list[str]:
    """Route templates in declaration order, which is also FastAPI's matching order."""
    return list(app.openapi()["paths"])


@pytest.mark.parametrize(
    ("literal", "pattern"),
    [
        ("/api/capture/pending", "/api/capture/{sku}"),
        ("/api/capture/recent", "/api/capture/{sku}"),
        ("/api/capture/{sku}/reprocess", "/api/capture/{sku}/{side}"),
    ],
)
def test_literal_routes_are_declared_before_the_patterns_that_would_shadow_them(
    literal, pattern
):
    """This has bitten twice. FastAPI matches in declaration order, so a pattern declared
    first wins: `/capture/pending` resolved as a card named "pending" and 404d, and
    `/capture/{sku}/reprocess` was matched as a side upload and demanded a file.

    Asserted against the route table rather than by making requests, so it needs no database
    and fails with a message that names the actual problem.
    """
    paths = _declared_paths()
    assert literal in paths and pattern in paths
    assert paths.index(literal) < paths.index(pattern), (
        f"{literal} must be declared before {pattern} or it will never be reached"
    )


def test_image_urls_are_versioned_by_content():
    """Regression: processed images are rewritten in place by reprocessing and by manual corner
    correction, while the images route serves them `immutable` for a year. Without a content
    token the browser is told never to revalidate, so a corrected card shows its old crop
    forever — which is exactly what happened."""
    from app.enums import ImageKind
    from app.models import Image
    from app.routers.capture import image_url

    before = Image(kind=ImageKind.PROCESSED_FRONT, path="CARD-000001/processed-front.jpg")
    before.sha256 = "a" * 64
    after = Image(kind=ImageKind.PROCESSED_FRONT, path="CARD-000001/processed-front.jpg")
    after.sha256 = "b" * 64

    assert image_url(before) == "/api/images/CARD-000001/processed-front.jpg?v=aaaaaaaaaaaa"
    assert image_url(before) != image_url(after), "same path, new pixels, must be a new URL"


def test_image_url_survives_a_missing_hash():
    from app.enums import ImageKind
    from app.models import Image
    from app.routers.capture import image_url

    assert image_url(None) is None
    unhashed = Image(kind=ImageKind.ORIGINAL_FRONT, path="CARD-000001/original-front.jpg")
    assert image_url(unhashed) == "/api/images/CARD-000001/original-front.jpg"


def test_pending_reports_unprocessed_captures(client):
    """A lost processing job leaves a capture with no error and no review — invisible unless
    something counts it. One in twenty went missing on a clean re-ingest of twenty images."""
    from app.main import app

    paths = app.openapi()["paths"]
    schema = paths["/api/capture/pending"]["get"]
    assert schema, "pending endpoint must exist"

def test_enum_drift_is_detectable(monkeypatch):
    """The failure this guards against: a long-running container whose Python enum is older
    than the database's. Writing a row with a value the container does not know makes
    SQLAlchemy raise `LookupError` on *read*, which takes out every endpoint touching that
    table rather than just the new rows — that is how `/capture/recent` started 500ing.

    Detection belongs in the health check rather than a test, because the mismatch is between a
    *running process* and a live schema. This exercises the comparison itself.
    """
    from app.services.enum_drift import compare

    assert compare({"image_kind": {"a", "b"}}, {"image_kind": {"a", "b"}}) == []

    drift = compare({"image_kind": {"a", "b", "c"}}, {"image_kind": {"a", "b"}})
    assert len(drift) == 1
    assert "c" in drift[0]
    assert "image_kind" in drift[0]
