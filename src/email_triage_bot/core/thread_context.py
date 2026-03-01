from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies, normalize_whitespace


@dataclass(frozen=True)
class ThreadItem:
    message_id: str
    internal_ts_ms: int
    from_hdr: str
    subject: str
    is_unread: bool
    is_sent: bool
    body: str


def parse_thread(thread: dict) -> list[ThreadItem]:
    items: list[ThreadItem] = []
    for m in thread.get("messages", []) or []:
        mid = m.get("id") or ""
        internal = int(m.get("internalDate") or 0)
        payload = m.get("payload", {}) or {}
        headers = payload.get("headers", []) or []
        from_hdr = get_header(headers, "From") or ""
        subject = get_header(headers, "Subject") or ""
        label_ids = set(m.get("labelIds", []) or [])
        is_unread = "UNREAD" in label_ids
        is_sent = "SENT" in label_ids

        text_plain, text_html = extract_bodies(payload)
        body = text_plain or (html_to_text(text_html) if text_html else "")
        body = strip_quoted_replies(body)

        items.append(ThreadItem(
            message_id=mid,
            internal_ts_ms=internal,
            from_hdr=from_hdr,
            subject=subject,
            is_unread=is_unread,
            is_sent=is_sent,
            body=body,
        ))
    items.sort(key=lambda x: x.internal_ts_ms)
    return items


def build_thread_context(items: Iterable[ThreadItem], max_items: int = 6, max_chars: int = 2200) -> str:
    tail = list(items)[-max_items:]
    blocks = []
    for it in tail:
        snippet = normalize_whitespace(it.body)
        if len(snippet) > 600:
            snippet = snippet[:600].rstrip() + "…"
        blocks.append(f"From: {it.from_hdr}\n{snippet}")
    out = "\n\n---\n\n".join(blocks).strip()
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out
