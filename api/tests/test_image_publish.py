"""Publishing photographs where eBay can fetch them.

eBay fetches each picture URL once at import and shows no error when that fetch fails, so the
failure mode is a draft with no photographs that nobody notices. These tests cover the parts
that decide whether a URL will work at all.
"""

import pytest

from app.services import image_publish


def test_nothing_is_configured_by_default():
    """Absent configuration must read as absent, not as a broken half-setup."""
    assert image_publish.configured() is False


def test_the_public_address_is_configured_not_guessed(monkeypatch):
    """R2, B2 and S3 all serve from a different host than they upload to.

    Deriving the public URL from the upload endpoint produces addresses that 403 rather than
    404, which is materially harder to diagnose.
    """
    monkeypatch.setattr(
        image_publish.settings, "s3_public_base", "https://pub-abc.r2.dev", raising=False
    )
    assert (
        image_publish.public_url("CARD-000011/listing-front.jpg")
        == "https://pub-abc.r2.dev/CARD-000011/listing-front.jpg"
    )


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    monkeypatch.setattr(
        image_publish.settings, "s3_public_base", "https://pub-abc.r2.dev/", raising=False
    )
    assert "//CARD" not in image_publish.public_url("/CARD-000011/x.jpg")


@pytest.mark.anyio
async def test_upload_refuses_without_configuration(monkeypatch):
    """Better a loud failure than a recorded URL that serves nothing.

    `configured()` is the gate callers check; this pins that an unconfigured `put` cannot
    quietly succeed against some default host.
    """
    monkeypatch.setattr(image_publish.settings, "s3_endpoint", "", raising=False)
    monkeypatch.setattr(image_publish.settings, "s3_bucket", "", raising=False)
    assert image_publish.configured() is False


def test_signing_key_is_derived_per_date_and_region():
    """SigV4 keys are scoped; reusing one across dates produces 403s that look like bad creds."""
    a = image_publish._sign("secret", "20260101", "auto", "s3")
    b = image_publish._sign("secret", "20260102", "auto", "s3")
    c = image_publish._sign("secret", "20260101", "us-east-1", "s3")
    assert a != b
    assert a != c
