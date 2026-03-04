from __future__ import annotations

import argparse
import re

from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies
from email_triage_bot.core.thread_context import parse_thread, build_thread_context
from email_triage_bot.core.prompt_builder import DEFAULT_MASTER_PROMPT, PromptParts, build_prompt
from email_triage_bot.clients.gemini.client import GeminiClient, GeminiConfig, GeminiRateLimitExceeded
from email_triage_bot.profiles import get_profile


def _contains_any_keyword(text: str, keywords_csv: str) -> bool:
    haystack = (text or "").lower()
    kws = [k.strip().lower() for k in (keywords_csv or "").split(",") if k.strip()]
    if not kws:
        return False
    return any(k in haystack for k in kws)


def _contains_name_keyword(text: str, keywords_csv: str) -> bool:
    haystack = (text or "")
    kws = [k.strip() for k in (keywords_csv or "").split(",") if k.strip()]
    if not kws:
        return True
    for k in kws:
        if re.search(rf"\b{re.escape(k)}\b", haystack, flags=re.IGNORECASE):
            return True
    return False


def _is_ignored_sender(from_header: str) -> bool:
    normalized = (from_header or "").lower()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return ("noreply" in compact) or ("noreplay" in compact) or ("announcements@" in normalized)


def _dedupe_threads(listed):
    by_thread = {}
    for m in listed:
        tid = m.thread_id or m.message_id
        cur = by_thread.get(tid)
        if cur is None or m.internal_ts_ms > cur.internal_ts_ms:
            by_thread[tid] = m
    return list(by_thread.values())


def _pick_latest_unread_inbound_message_id(gmail: GmailClient, thread_id: str) -> str | None:
    thread = gmail.get_thread_full(thread_id)
    items = parse_thread(thread)
    for it in reversed(items):
        if it.is_unread and not it.is_sent and (it.body or "").strip():
            return it.message_id
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--dry-run", action="store_true", help="Do everything except create drafts")
    ap.add_argument("--limit", type=int, default=None, help="Override BATCH_LIMIT")
    ap.add_argument("--query", type=str, default=None, help="Override GMAIL_QUERY")
    ap.add_argument("--no-thread-dedupe", action="store_true", help="Disable thread dedupe")
    ap.add_argument("--no-mark-read", action="store_true", help="Do NOT mark message as read after drafting")
    args = ap.parse_args()

    s = Settings()
    setup_logging(s.log_level)

    if not s.gemini_api_key:
        raise SystemExit("Missing GEMINI_API_KEY in .env")

    profile_name = args.profile.strip() or s.default_profile
    prof = get_profile(s.profiles_path, profile_name)

    credentials_path = prof.credentials_path if prof else s.gmail_credentials_path
    token_path = prof.token_path if prof else s.gmail_token_path
    signature = prof.draft_signature if prof and prof.draft_signature is not None else s.draft_signature

    query = (args.query.strip() if args.query else (prof.gmail_query if prof and prof.gmail_query else s.gmail_query))
    limit = int(args.limit if args.limit is not None else (prof.batch_limit if prof and prof.batch_limit else s.batch_limit))
    dry_run = bool(args.dry_run)
    dedupe = not bool(args.no_thread_dedupe)
    mark_read = (not args.no_mark_read) and (not dry_run)

    gmail = GmailClient(
        credentials_path=credentials_path,
        token_path=token_path,
        include_compose_scope=not dry_run,
        include_modify_scope=mark_read,
    )

    listed = gmail.list_messages(query=query, limit=limit)
    before = len(listed)
    threads = _dedupe_threads(listed) if dedupe else listed
    after = len(threads)

    print(f"account: {profile_name} | Batch found: {before} (threads: {after}) | query: {query} | limit: {limit} | dry_run: {dry_run} | mark_read: {mark_read}")

    with GeminiClient(GeminiConfig(
        api_key=s.gemini_api_key,
        model=s.gemini_model,
        api_version=s.gemini_api_version,
        timeout_s=s.gemini_timeout_s,
    )) as llm:

        drafted = 0
        marked = 0
        skipped = 0
        failed = 0

        for t in sorted(threads, key=lambda x: x.internal_ts_ms, reverse=True):
            thread_id = t.thread_id

            try:
                target_id = _pick_latest_unread_inbound_message_id(gmail, thread_id)
            except Exception as ex:
                failed += 1
                print(f"FAILED (thread read): {thread_id} | {type(ex).__name__}: {ex}")
                continue

            if not target_id:
                skipped += 1
                print(f"SKIP (no unread inbound in thread): {thread_id}")
                continue

            msg = gmail.get_message_full(target_id)
            payload = msg.get("payload", {}) or {}
            headers = payload.get("headers", []) or []
            subject = get_header(headers, "Subject") or ""
            from_hdr = get_header(headers, "From") or ""

            text_plain, text_html = extract_bodies(payload)
            body_text = text_plain or (html_to_text(text_html) if text_html else "")
            body_text = strip_quoted_replies(body_text).strip()

            if _is_ignored_sender(from_hdr):
                skipped += 1
                print(f"SKIP (ignored sender): {target_id} | {subject[:80]}")
                continue

            ignore_hay = f"{from_hdr}\n{subject}\n{body_text}"
            if _contains_any_keyword(ignore_hay, s.ignore_keywords):
                skipped += 1
                print(f"SKIP (ignore keyword): {target_id} | {subject[:80]}")
                continue

            if s.require_name_mention:
                hay = f"{subject}\n{body_text}"
                if not _contains_any_keyword(hay, s.name_keywords):
                    skipped += 1
                    print(f"SKIP (no name mention): {target_id} | {subject[:80]}")
                    continue

            thread_context = ""
            try:
                thread = gmail.get_thread_full(thread_id)
                items = parse_thread(thread)
                thread_context = build_thread_context(items, max_items=6, max_chars=2200)
            except Exception:
                thread_context = ""

            latest_block = f"From: {from_hdr}\nSubject: {subject}\n\n{body_text}"
            prompt = build_prompt(PromptParts(
                master=DEFAULT_MASTER_PROMPT,
                thread_context=thread_context,
                latest_email=latest_block,
            ))

            try:
                draft_body = llm.generate(prompt)
            except GeminiRateLimitExceeded as ex:
                print("WARNING: Gemini free-tier limit reached (429 RESOURCE_EXHAUSTED).")
                print("Stopping batch now. Retry later, or reduce batch size/frequency.")
                print(f"Details: {ex}")
                break
            except Exception as ex:
                failed += 1
                print(f"FAILED (Gemini): {target_id} | {type(ex).__name__}: {ex}")
                continue

            if signature and signature.strip():
                draft_body = draft_body.rstrip() + "\n\n" + signature.strip() + "\n"

            if dry_run:
                print(f"[DRY RUN] WOULD DRAFT: {target_id} | {subject[:80]}")
                continue

            try:
                gmail.create_reply_draft(message_id=target_id, draft_text=draft_body)
                drafted += 1
                print(f"DRAFTED: {target_id} | {subject[:80]}")
            except Exception as ex:
                failed += 1
                print(f"FAILED (draft): {target_id} | {type(ex).__name__}: {ex}")
                continue

            if mark_read:
                try:
                    gmail.mark_as_read(target_id)
                    marked += 1
                    print(f"MARKED READ: {target_id}")
                except Exception as ex:
                    print(f"WARNING (mark read failed): {target_id} | {type(ex).__name__}: {ex}")

        print("----")
        print(f"Done. drafted={drafted} marked_read={marked} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
