"""HTTP fetching that behaves the same on every supported Python.

Python's urllib gained 308 (Permanent Redirect) handling in 3.11. On 3.9 and
3.10 a 308 is raised as an HTTPError instead of followed, and zid.sa answers
every URL without a trailing slash with exactly that. The result was a crawl
that fetched five pages out of a hundred on macOS while fetching all hundred
on the developer's machine — the kind of difference that looks like a
website problem rather than a runtime one.

Errors carry the status code. "HTTPError" on its own says nothing about
whether the fix is a trailing slash, a login, or a rate limit.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

USER_AGENT = "ask-zid-ingest/0.1 (+internal knowledge base)"


class _Follow308(urllib.request.HTTPRedirectHandler):
    """Treat 308 as 301, which is what Python 3.11+ does natively."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)


_opener = urllib.request.build_opener(_Follow308)


def get(url: str, timeout: int = 45, retries: int = 3) -> str:
    """Fetch a URL as text, following redirects, retrying what is worth retrying."""
    last = ""
    for attempt in range(retries):
        try:
            with _opener.open(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=timeout,
            ) as response:
                return response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            # 4xx other than rate limiting will not become valid on a retry.
            if exc.code < 429 or attempt == retries - 1:
                raise RuntimeError(f"{url}: {last}") from None
        except Exception as exc:
            last = type(exc).__name__
            if attempt == retries - 1:
                raise RuntimeError(f"{url}: {last}") from None
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: {last}")
