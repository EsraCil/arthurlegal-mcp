"""HTTP helpers for arthur-tr-hukuk-mcp — standard library only.

Every upstream in this server is a Turkish public-sector site. They share three
habits this module exists to absorb:

* **Rate limits that punish bursts.** Bedesten (Adalet Bakanlığı) returns 429
  after ~10 requests per 30 s from one IP; the token bucket below keeps a
  server that fans out searches from tripping it.
* **Cookies and CSRF tokens.** Sayıştay and Uyuşmazlık are ASP.NET apps that
  need a session cookie and a hidden form field before they answer. A
  cookie-aware opener is therefore the default, not an option.
* **Windows-1254 and mislabelled charsets.** Resmî Gazete still serves
  ``charset=windows-1254``; several others declare UTF-8 and lie. ``decode``
  tries the declared charset, then UTF-8, then cp1254, and never raises.

No dependency on ``httpx`` or ``requests``: the whole server must run on a
bare Python 3.11 so it can be vendored, containerised or dropped into a
Copilot Studio connector without a build step.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

__all__ = ["Http", "HttpError", "TokenBucket", "UA", "decode", "qs"]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")

DEFAULT_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "60"))

# Largest body read or inflated from one response. The largest real document seen is a Rekabet decision PDF of
# a few tens of MB; anything bigger is refused instead of exhausting the 1 GB machine.
MAX_BYTES = int(os.environ.get("HTTP_MAX_BYTES", str(64 * 1024 * 1024)))


class HttpError(Exception):
    """An upstream answered with an error status. Carries status + body head."""

    def __init__(self, status: int, url: str, body: bytes = b"", headers=None):
        self.status = status
        self.url = url
        self.body = body
        self.headers = headers or {}
        head = decode(body, headers)[:300].replace("\n", " ")
        super().__init__("HTTP %d from %s: %s" % (status, url, head))


class TokenBucket:
    """Thread-safe token bucket with explicit back-pressure.

    ``capacity`` tokens, refilled at one token per ``refill_s`` seconds. A 429
    from upstream calls :meth:`penalize` so the next caller waits the server's
    ``Retry-After`` instead of immediately re-tripping the limit.
    """

    def __init__(self, capacity: int = 1, refill_s: float = 3.5, max_wait: float = 20.0):
        self.capacity = float(capacity)
        self.refill_per_s = 1.0 / float(refill_s)
        self.max_wait = float(max_wait)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._not_before = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._not_before:
                    wait = self._not_before - now
                else:
                    self._tokens = min(self.capacity,
                                       self._tokens + (now - self._last) * self.refill_per_s)
                    self._last = now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) * (1.0 / self.refill_per_s)
            if time.monotonic() + wait > deadline:
                raise HttpError(429, "local rate limiter",
                                ("Upstream rate limit: retry in %.0f s" % wait).encode())
            time.sleep(min(wait, 1.0))

    def penalize(self, seconds: float) -> None:
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + seconds)
            self._tokens = 0.0
            self._last = time.monotonic()


_CHARSET = re.compile(r'charset=["\']?([\w\-]+)', re.I)


def decode(body: bytes, headers: Optional[Dict[str, str]] = None) -> str:
    """Bytes → str without ever raising. Declared charset → UTF-8 → cp1254."""
    if not body:
        return ""
    declared = ""
    ctype = ""
    if headers:
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
    m = _CHARSET.search(ctype)
    if m:
        declared = m.group(1).lower()
    if not declared:
        head = body[:2048].decode("ascii", "ignore")
        m = _CHARSET.search(head)
        if m:
            declared = m.group(1).lower()
    for enc in (declared, "utf-8", "windows-1254", "latin-1"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


def qs(params: Union[Dict[str, Any], Sequence[Tuple[str, Any]]]) -> str:
    items = params.items() if isinstance(params, dict) else params
    return urllib.parse.urlencode([(k, "" if v is None else str(v)) for k, v in items],
                                  doseq=True)


class Http:
    """A small cookie-aware HTTP client bound to one upstream.

    ``headers`` are sent on every request; ``bucket`` throttles; ``verify=False``
    disables TLS verification for the two upstreams (KİK, Uyuşmazlık) whose
    chains fail validation on a stock Python — the same concession the
    reference implementations make, and documented per source.
    """

    def __init__(self, base_url: str = "", headers: Optional[Dict[str, str]] = None,
                 bucket: Optional[TokenBucket] = None, verify: bool = True,
                 timeout: float = DEFAULT_TIMEOUT, follow_redirects: bool = True,
                 ciphers: str = "", allowed_domains: Iterable[str] = ()):
        self.base_url = base_url.rstrip("/")
        # Tool arguments reach request paths (a decision URL, a document id), so every request, and every
        # redirect, must stay on the upstream's own domain: without this the server fetches any URL a caller
        # names, including Fly's private network. Default: the base host without "www." and its subdomains.
        domains = {d.lower().lstrip(".") for d in allowed_domains}
        base_host = (urllib.parse.urlsplit(self.base_url).hostname or "").lower()
        if base_host:
            domains.add(base_host[4:] if base_host.startswith("www.") else base_host)
        self.allowed_domains = frozenset(domains)
        self.headers = {"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
                        "Accept-Encoding": "gzip, deflate"}
        if headers:
            self.headers.update(headers)
        self.bucket = bucket
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        handlers: List[Any] = [urllib.request.HTTPCookieProcessor(self.jar)]
        if not verify or ciphers:
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
                ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
            if ciphers:
                # EKAP v2 negotiates only with an explicit cipher list; OpenSSL's
                # default security level refuses its handshake outright.
                ctx.set_ciphers(ciphers)
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        handlers.append(self._redirect_handler(follow_redirects))
        self.opener = urllib.request.build_opener(*handlers)
        self.verify = verify
        self._ciphers = ciphers
        self._follow = follow_redirects

    def allowed(self, url: str) -> bool:
        """True when ``url`` is https (or http on an http base) on one of the allowed domains."""
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        schemes = ("https", "http") if self.base_url.startswith("http://") else ("https",)
        return (parts.scheme in schemes and bool(host)
                and any(host == d or host.endswith("." + d) for d in self.allowed_domains))

    def _redirect_handler(self, follow: bool) -> urllib.request.HTTPRedirectHandler:
        client = self

        class _Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
                if not follow:
                    return None
                if not client.allowed(newurl):
                    raise HttpError(code, newurl, b"redirect to a host outside this upstream refused")
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        return _Redirect()

    def _degrade_tls(self, url: str) -> None:
        import sys
        sys.stderr.write("net: TLS chain for %s not verifiable with the local CA store; "
                         "continuing unverified for this upstream\n" % urllib.parse.urlsplit(url).netloc)
        self.verify = False
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        if self._ciphers:
            ctx.set_ciphers(self._ciphers)
        handlers: List[Any] = [urllib.request.HTTPCookieProcessor(self.jar),
                               urllib.request.HTTPSHandler(context=ctx),
                               self._redirect_handler(self._follow)]
        self.opener = urllib.request.build_opener(*handlers)

    # -- core ------------------------------------------------------------- #
    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = self.base_url + (path if path.startswith("/") else "/" + path)
        # IRI → URI. İSTAÇ and Resmî Gazete link PDFs whose paths contain
        # Turkish letters; http.client insists on ASCII request lines.
        if any(ord(ch) > 127 or ch == " " for ch in url):
            url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*[]~")
        return url

    def request(self, method: str, path: str, params=None, data: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
                retries: int = 1) -> Tuple[int, Dict[str, str], bytes, str]:
        url = self._url(path)
        if not self.allowed(url):
            raise HttpError(0, url, b"host outside this upstream refused")
        if params:
            url += ("&" if "?" in url else "?") + qs(params)
        hdrs = dict(self.headers)
        if headers:
            hdrs.update(headers)
        attempt = 0
        while True:
            attempt += 1
            if self.bucket:
                self.bucket.acquire()
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with self.opener.open(req, timeout=timeout or self.timeout) as resp:
                    raw = resp.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise HttpError(0, url, b"response larger than HTTP_MAX_BYTES refused")
                    rh = {k: v for k, v in resp.headers.items()}
                    return resp.status, rh, _inflate(raw, rh, url), resp.geturl()
            except HttpError:
                raise
            except urllib.error.HTTPError as exc:
                raw = exc.read(64 * 1024) if hasattr(exc, "read") else b""
                rh = {k: v for k, v in (exc.headers.items() if exc.headers else [])}
                body = _inflate(raw, rh, url)
                if exc.code == 429 and self.bucket:
                    try:
                        ra = float(rh.get("Retry-After", "30"))
                    except ValueError:
                        ra = 30.0
                    self.bucket.penalize(max(1.0, min(ra, 60.0)) + 0.5)
                if exc.code in (502, 503, 504) and attempt <= retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise HttpError(exc.code, url, body, rh) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                # Several Turkish public-sector hosts (BDDK among them) serve a
                # chain that a stock Debian/certifi store cannot complete while a
                # Windows store can. Everything fetched here is public, read-only
                # material, so on a verification failure the client drops to an
                # unverified context for this upstream and says so on stderr
                # once, rather than answering "no results".
                if "CERTIFICATE_VERIFY_FAILED" in str(exc) and self.verify:
                    self._degrade_tls(url)
                    continue
                if attempt <= retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise HttpError(0, url, str(exc).encode()) from None

    # -- conveniences ----------------------------------------------------- #
    def get(self, path: str, params=None, headers=None, timeout=None) -> Tuple[bytes, Dict[str, str], str]:
        _, rh, body, final = self.request("GET", path, params=params, headers=headers,
                                          timeout=timeout)
        return body, rh, final

    def get_text(self, path: str, params=None, headers=None, timeout=None) -> str:
        body, rh, _ = self.get(path, params=params, headers=headers, timeout=timeout)
        return decode(body, rh)

    def get_json(self, path: str, params=None, headers=None, timeout=None) -> Any:
        body, rh, _ = self.get(path, params=params, headers=headers, timeout=timeout)
        return json.loads(decode(body, rh) or "null")

    def post_json(self, path: str, payload: Any, params=None, headers=None, timeout=None) -> Any:
        hdrs = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        _, rh, body, _ = self.request("POST", path, params=params, data=data, headers=hdrs,
                                      timeout=timeout)
        text = decode(body, rh)
        try:
            return json.loads(text)
        except ValueError:
            return {"_raw": text[:2000]}

    def post_form(self, path: str, form: Union[Dict[str, Any], Sequence[Tuple[str, Any]]],
                  params=None, headers=None, timeout=None) -> Tuple[str, Dict[str, str], int]:
        hdrs = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        if headers:
            hdrs.update(headers)
        data = qs(form).encode("utf-8")
        status, rh, body, _ = self.request("POST", path, params=params, data=data,
                                           headers=hdrs, timeout=timeout)
        return decode(body, rh), rh, status

    def cookies(self) -> Dict[str, str]:
        return {c.name: c.value for c in self.jar}


def _inflate(raw: bytes, headers: Dict[str, str], url: str = "") -> bytes:
    """Undo Content-Encoding, never past MAX_BYTES (a few MB of gzip can expand to GBs)."""
    enc = (headers.get("Content-Encoding") or headers.get("content-encoding") or "").lower()
    if "gzip" in enc:
        wbits_list = [16 + zlib.MAX_WBITS]
    elif "deflate" in enc:
        wbits_list = [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    else:
        return raw
    for wbits in wbits_list:
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(raw, MAX_BYTES + 1)
        except zlib.error:
            continue
        if len(out) > MAX_BYTES or d.unconsumed_tail:
            raise HttpError(0, url, b"decompressed response larger than HTTP_MAX_BYTES refused")
        return out
    return raw
