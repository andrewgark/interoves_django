"""Secret-safe Yandex Direct v501 + Metrika HTTP client."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from interoves_django.settings import load_secret

DIRECT_JSON = "https://api.direct.yandex.com/json"
METRIKA_API = "https://api-metrika.yandex.net"
LOGIN_INFO = "https://login.yandex.ru/info"


class DirectApiError(RuntimeError):
    def __init__(self, message: str, *, payload: dict | None = None, http_status: int | None = None):
        super().__init__(message)
        self.payload = payload
        self.http_status = http_status


def _oauth_token() -> str:
    token = load_secret("yandex_api_oauth_token.txt", env_var="YANDEX_API_OAUTH_TOKEN")
    if not token:
        raise DirectApiError("Yandex API OAuth token is missing (env or secrets file).")
    return token


def redact(text: str, token: str | None = None) -> str:
    if not text:
        return text
    secret = token or ""
    if secret:
        text = text.replace(secret, "[REDACTED]")
    lowered = text
    for prefix in ("Bearer ", "OAuth "):
        idx = 0
        while True:
            found = lowered.find(prefix.lower(), idx)
            if found < 0:
                break
            # Operate on original string using the same offset (ASCII prefixes).
            start = found + len(prefix)
            end = start
            while end < len(text) and not text[end].isspace() and text[end] not in '",}':
                end += 1
            text = text[:start] + "[REDACTED]" + text[end:]
            lowered = text
            idx = start + len("[REDACTED]")
    return text


class AdsClient:
    def __init__(self, *, client_login: str | None = None, timeout: int = 120):
        self.token = _oauth_token()
        self.client_login = client_login
        self.timeout = timeout
        self.last_units: str | None = None

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.client_login:
            headers["Client-Login"] = self.client_login
        if extra:
            headers.update(extra)
        return headers

    def _read_http_error(self, exc: HTTPError) -> tuple[int, str, Any]:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        return exc.code, redact(raw, self.token), parsed

    def call(self, service: str, method: str, params: dict | None = None, *, version: str = "v501") -> dict:
        url = f"{DIRECT_JSON}/{version}/{service}"
        body = json.dumps({"method": method, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=body, method="POST", headers=self._headers())
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                self.last_units = resp.headers.get("Units")
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            status, raw, parsed = self._read_http_error(exc)
            message = _direct_error_message(parsed) or f"Direct HTTP {status}"
            raise DirectApiError(redact(message, self.token), payload=parsed, http_status=status) from None
        except URLError as exc:
            raise DirectApiError(f"Direct network error: {exc.reason}") from None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DirectApiError("Direct returned non-JSON") from exc
        if "error" in payload:
            raise DirectApiError(_direct_error_message(payload), payload=payload)
        return payload.get("result", payload)

    def reports(self, params: dict, *, retries: int = 20, version: str = "v5") -> list[dict]:
        url = f"{DIRECT_JSON}/{version}/reports"
        body = json.dumps({"params": params}, ensure_ascii=False).encode("utf-8")
        headers = self._headers(
            {
                "processingMode": "auto",
                "returnMoneyInMicros": "false",
                "skipReportHeader": "true",
                "skipReportSummary": "true",
            }
        )
        last_raw = ""
        for _ in range(retries):
            req = Request(url, data=body, method="POST", headers=headers)
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    status = resp.status
                    raw = resp.read().decode("utf-8")
                    if status in (201, 202):
                        time.sleep(2)
                        continue
                    return _parse_tsv_report(raw)
            except HTTPError as exc:
                status, raw, parsed = self._read_http_error(exc)
                last_raw = raw
                if status in (201, 202):
                    time.sleep(2)
                    continue
                message = _direct_error_message(parsed) or f"Reports HTTP {status}"
                raise DirectApiError(redact(message, self.token), payload=parsed, http_status=status) from None
        raise DirectApiError(f"Reports still processing after {retries} retries: {last_raw[:200]}")

    def metrika_get(self, path: str, query: dict | None = None) -> dict:
        url = f"{METRIKA_API}{path}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        req = Request(
            url,
            headers={
                "Authorization": f"OAuth {self.token}",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            status, raw, parsed = self._read_http_error(exc)
            message = ""
            if isinstance(parsed, dict):
                message = parsed.get("message") or json.dumps(parsed.get("errors", parsed), ensure_ascii=False)
            raise DirectApiError(redact(message or f"Metrika HTTP {status}", self.token), payload=parsed, http_status=status) from None
        return json.loads(raw)

    def login_info(self) -> dict:
        req = Request(
            f"{LOGIN_INFO}?format=json",
            headers={"Authorization": f"OAuth {self.token}"},
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            status, raw, parsed = self._read_http_error(exc)
            raise DirectApiError(f"login.yandex.ru HTTP {status}", payload=parsed, http_status=status) from None
        return {
            "id": data.get("id"),
            "login": data.get("login"),
            "display_name": data.get("display_name"),
            "client_id": data.get("client_id"),
        }


def _direct_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error") or payload
    if isinstance(err, dict):
        parts = [
            str(err.get("error_string") or err.get("error_code") or ""),
            str(err.get("error_detail") or err.get("error_description") or ""),
        ]
        return ": ".join(p for p in parts if p)
    return ""


def _parse_tsv_report(raw: str) -> list[dict]:
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return []
    headers = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        values = line.split("\t")
        rows.append({headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))})
    return rows


def default_client() -> AdsClient:
    # This advertiser is a CLIENT, not an agency. Do not send Client-Login.
    _ = settings.BASE_DIR
    return AdsClient(client_login=None)
