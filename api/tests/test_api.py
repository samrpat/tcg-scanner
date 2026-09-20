"""API surface. Only endpoints that need no database are exercised here; the rest are
covered once the stack is up (see the Phase 1 verification notes in CURRENT.md)."""

import pytest
from fastapi.testclient import TestClient

from app.main import build_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    # Full mode deliberately: this file covers the whole API surface, and the router set must
    # not depend on how the container running the tests happens to be configured.
    #
    # Authentication off: these tests are about the shape of the API, and a fixture that had to
    # log in first would be testing the front door in every one of them. The front door has its
    # own tests, including one pinning the default on.
    return TestClient(build_app(scanner=False, auth_required=False))


@pytest.fixture(scope="module")
def scanner_client() -> TestClient:
    return TestClient(build_app(scanner=True, auth_required=False))


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
    return list(build_app(scanner=False, auth_required=False).openapi()["paths"])


@pytest.mark.parametrize(
    ("literal", "pattern"),
    [
        ("/api/capture/pending", "/api/capture/{sku}"),
        ("/api/capture/recent", "/api/capture/{sku}"),
        ("/api/capture/{sku}/reprocess", "/api/capture/{sku}/{side}"),
        ("/api/capture/{sku}/detail-shots", "/api/capture/{sku}/{side}"),
        ("/api/capture/{sku}/extra", "/api/capture/{sku}/{side}"),
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
    from app.main import build_app

    paths = build_app(scanner=False, auth_required=False).openapi()["paths"]
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


def test_scanner_mode_serves_the_scanner_and_nothing_else(scanner_client, client):
    """The scanner build must keep every route the scanning loop uses, and drop the rest.

    Not cosmetic. This app has no authentication, so a mounted route is an open route — and
    the listing half includes deleting inventory and publishing photographs to the internet.
    A screen being hidden in the UI is not the same as the endpoint being unreachable, and the
    two drifting apart is exactly the kind of thing nobody notices.
    """
    scanning = [
        "/api/capture/recent",
        "/api/capture/pending",
        "/api/sessions",
        "/api/inventory",
        "/api/images/{path}",
        "/api/jobs",
        "/api/catalog/stats",
        # Corner close-ups are the thing this system does that no listing tool does, so every
        # route that makes or removes them has to exist in the build whose whole job is making
        # them. They lived on the eBay router once, which is not mounted here at all — the one
        # screen entirely about corner crops was calling endpoints that answered 404.
        "/api/capture/{sku}/detail-shots",
        "/api/sessions/{session_id}/corners",
        # Extra shots and batch renaming are scanner work by definition.
        "/api/capture/extra",
        "/api/capture/{sku}/extra",
        "/api/capture/{sku}/extra/{number}",
        "/api/sessions/{session_id}",
    ]
    paths = scanner_client.app.openapi()["paths"]
    for path in scanning:
        assert path in paths, f"scanner mode must serve {path}"
    for path in paths:
        assert not path.startswith(("/api/ebay", "/api/review", "/api/conditioning")), (
            f"scanner mode must not serve {path}"
        )

    # ...and full mode is still whole.
    full = client.app.openapi()["paths"]
    assert any(p.startswith("/api/ebay") for p in full)
    assert any(p.startswith("/api/conditioning") for p in full)
    assert all(p in full for p in scanning)


def test_the_shutter_key_never_fires_while_someone_is_typing():
    """Both camera screens bind Space and Enter to the shutter so a Bluetooth remote works as
    a pedal. Any text field on the same screen then fires the camera on every space — and
    because the handler calls preventDefault, the space does not even reach the field.

    Naming a section on the scan screen was impossible to type and took a photograph per word.
    Extras had a partial guard; Scan had none. Asserted against the source because this is a
    browser-event property with no Python to exercise.
    """
    from pathlib import Path

    # Mounted at /web by `make test`; parents[2] resolves there from /srv/tests.
    web = Path(__file__).resolve().parents[2] / "web" / "src"
    assert web.is_dir(), "web/ is not mounted — see the `test` target in the Makefile"
    shared = (web / "keys.ts").read_text()
    for kind in ("HTMLInputElement", "HTMLTextAreaElement", "HTMLSelectElement"):
        assert kind in shared, f"the typing guard ignores {kind}"
    assert "isContentEditable" in shared

    for screen in ("Scan.tsx", "Extras.tsx"):
        source = (web / screen).read_text()
        assert 'from "./keys"' in source, f"{screen} does not use the shared guard"
        # The guard must come before preventDefault, or the keystroke is eaten anyway.
        guard = source.index("isTyping(e)")
        assert guard < source.index("e.preventDefault()"), f"{screen} guards too late"
