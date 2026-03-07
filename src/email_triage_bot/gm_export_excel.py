from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook

from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.config import Settings
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.profiles import get_profile


def _to_iso_utc(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="Export sender/subject/body to an Excel file for triage analysis.")
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--limit", type=int, default=None, help="Override BATCH_LIMIT")
    ap.add_argument("--query", type=str, default=None, help="Override GMAIL_QUERY")
    ap.add_argument("--output", type=str, default="triage_export.xlsx", help="Output .xlsx path")
    ap.add_argument("--include-quoted", action="store_true", help="Keep quoted replies in body")
    args = ap.parse_args()

    settings = Settings()
    setup_logging(settings.log_level)

    profile_name = args.profile.strip() or settings.default_profile
    profile = get_profile(settings.profiles_path, profile_name)

    credentials_path = profile.credentials_path if profile else settings.gmail_credentials_path
    token_path = profile.token_path if profile else settings.gmail_token_path
    query = args.query.strip() if args.query else (profile.gmail_query if profile and profile.gmail_query else settings.gmail_query)
    limit = int(args.limit if args.limit is not None else (profile.batch_limit if profile and profile.batch_limit else settings.batch_limit))

    gmail = GmailClient(
        credentials_path=credentials_path,
        token_path=token_path,
        include_compose_scope=False,
        include_modify_scope=False,
    )

    listed = gmail.list_messages(query=query, limit=limit)

    wb = Workbook()
    ws = wb.active
    ws.title = "emails"
    ws.append(["message_id", "thread_id", "date_utc", "from", "subject", "body", "gmail_link"])

    for item in listed:
        msg = gmail.get_message_full(item.message_id)
        payload = msg.get("payload", {}) or {}
        headers = payload.get("headers", []) or []

        subject = get_header(headers, "Subject") or item.subject or ""
        from_hdr = get_header(headers, "From") or item.from_address or ""
        text_plain, text_html = extract_bodies(payload)
        body = text_plain or (html_to_text(text_html) if text_html else "")
        if not args.include_quoted:
            body = strip_quoted_replies(body)
        body = (body or "").strip()

        ws.append([
            item.message_id,
            item.thread_id,
            _to_iso_utc(item.internal_ts_ms),
            from_hdr,
            subject,
            body,
            f"https://mail.google.com/mail/u/0/#inbox/{item.message_id}",
        ])

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    print(f"account: {profile_name} | exported: {len(listed)} | query: {query} | output: {out}")


if __name__ == "__main__":
    main()
