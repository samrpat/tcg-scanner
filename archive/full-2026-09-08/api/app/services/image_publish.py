"""Publishing card photographs somewhere eBay can reliably fetch them.

eBay's picture service fetches each URL **once, at import**, and copies the image to its own
storage. If that fetch fails the draft is created without photographs and without an error the
seller ever sees. So the only thing that matters about the host is that it answers, reliably,
during the minutes an import runs.

A Cloudflare quick tunnel is convenient and turned out not to be that. Observed, in one session:
a tunnel served correctly for hours, then lost its control stream while the container stayed
"running" and its metrics endpoint kept reporting the same hostname — which Cloudflare had by
then withdrawn from DNS. A freshly created replacement registered successfully and its hostname
never resolved at all. Both failures are silent from inside this application, which is the worst
property a picture host can have.

Object storage does not have that failure mode. This uploads to anything S3-compatible —
Cloudflare R2, Backblaze B2, Amazon S3, MinIO — using SigV4 signed requests written out by hand
rather than pulling in `boto3`, because the whole surface needed here is one PUT.

The keys are deterministic (`<sku>/<filename>`), so a re-upload replaces rather than
accumulating, and a published URL can be derived without a database lookup.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import urllib.parse
from dataclasses import dataclass

import httpx

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_UNRESERVED_SAFE = "-._~/"


@dataclass(frozen=True)
class PublishResult:
    uploaded: list[str]
    failed: list[dict]
    base: str | None

    def as_dict(self) -> dict:
        return {"uploaded": self.uploaded, "failed": self.failed, "base": self.base}


def configured() -> bool:
    return bool(
        settings.s3_endpoint
        and settings.s3_bucket
        and settings.s3_access_key
        and settings.s3_secret_key
    )


def public_url(key: str) -> str:
    """The address eBay will fetch. Explicit, because it is rarely the upload endpoint.

    R2 serves from a `*.r2.dev` address or a custom domain; B2 from `f00X.backblazeb2.com`;
    S3 from the bucket host. Guessing wrong produces URLs that 403 rather than 404, which is
    harder to diagnose, so the public base is configured rather than derived.
    """
    base = (settings.s3_public_base or "").rstrip("/")
    return f"{base}/{key.lstrip('/')}"


def _sign(secret: str, date: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret}".encode()
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


async def put(key: str, body: bytes, content_type: str = "image/jpeg") -> None:
    """Upload one object with a SigV4-signed PUT.

    Raises on any non-2xx so a caller never records a URL that does not serve — the entire
    point of moving off the tunnel was to stop pretending pictures exist.
    """
    endpoint = settings.s3_endpoint.rstrip("/")
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    region = settings.s3_region or "auto"

    canonical_uri = urllib.parse.quote(
        f"/{settings.s3_bucket}/{key.lstrip('/')}", safe=_UNRESERVED_SAFE
    )
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{date_stamp}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _sign(settings.s3_secret_key, date_stamp, region, "s3"),
        to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={settings.s3_access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.put(
            f"{parsed.scheme}://{host}{canonical_uri}",
            content=body,
            headers={
                **headers,
                "authorization": authorization,
                "content-type": content_type,
            },
        )
    if response.status_code >= 300:
        raise RuntimeError(
            f"upload failed: HTTP {response.status_code} {response.text[:200]}"
        )
