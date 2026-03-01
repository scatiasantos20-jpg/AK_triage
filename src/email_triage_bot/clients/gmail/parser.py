from __future__ import annotations

import base64


def get_header(headers: list[dict], name: str) -> str | None:
    name_l = (name or "").lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name_l:
            return h.get("value")
    return None


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    missing = (-len(data)) % 4
    if missing:
        data += "=" * missing
    try:
        raw = base64.urlsafe_b64decode(data.encode("utf-8"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_bodies(payload: dict) -> tuple[str, str]:
    if not payload:
        return "", ""

    mime_type = payload.get("mimeType") or ""
    body = payload.get("body", {}) or {}
    data = body.get("data") or ""

    if mime_type == "text/plain":
        return _b64url_decode(data), ""
    if mime_type == "text/html":
        return "", _b64url_decode(data)

    text_plain = ""
    text_html = ""
    for part in payload.get("parts", []) or []:
        p_plain, p_html = extract_bodies(part)
        if p_plain and not text_plain:
            text_plain = p_plain
        if p_html and not text_html:
            text_html = p_html

    return text_plain, text_html
