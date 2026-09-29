"""Reading a Gemini 429.

Gemini answers two different problems with the same status code:

  per minute  too many requests just now; the body says how long to wait,
              and waiting works
  per day     the free tier's daily allowance for this model is spent;
              nothing but tomorrow works

Retrying the second with backoff is the worst of both: every call waits out
its whole retry budget -- about half a minute -- before failing anyway, so a
document with thirty images takes a quarter of an hour to fail. Telling them
apart is what lets a caller stop at the first one.

The distinction is in the error details, as the id of the quota that was
exceeded, e.g. `GenerateRequestsPerDayPerProjectPerModel-FreeTier`.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.embedding.rate_limit import QuotaExhaustedError, RateLimitError

_QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"
_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"


def _details(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return []
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    return [d for d in details or [] if isinstance(d, dict)]


def _retry_after(
    response: httpx.Response, details: list[dict[str, Any]]
) -> float | None:
    for detail in details:
        if detail.get("@type") == _RETRY_INFO:
            # A protobuf Duration, rendered as "37s" or "0.5s".
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    hint = response.headers.get("retry-after", "")
    return float(hint) if hint.isdigit() else None


def rate_limit_error(
    response: httpx.Response, what: str
) -> RateLimitError | QuotaExhaustedError:
    """The exception a 429 from Gemini should raise.

    `what` names the caller in the message ("vision", "embedding").
    """
    details = _details(response)
    for detail in details:
        if detail.get("@type") != _QUOTA_FAILURE:
            continue
        for violation in detail.get("violations") or []:
            quota = str(violation.get("quotaId", ""))
            if "PerDay" in quota:
                return QuotaExhaustedError(
                    f"{what}: the free daily quota for this model is used up ({quota})"
                )

    return RateLimitError(
        f"{what} rate limited: {response.text[:200]}",
        retry_after=_retry_after(response, details),
    )
