from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies
from email_triage_bot.core.thread_context import parse_thread, build_thread_context
from email_triage_bot.core.prompt_builder import DEFAULT_MASTER_PROMPT, PromptParts, build_prompt
from email_triage_bot.clients.gemini.client import GeminiClient, GeminiConfig, GeminiRateLimitExceeded
from email_triage_bot.profiles import get_profile


DEFAULT_FILTER_RULES: dict = {
    "no_reply_senders": [],
    "newsletter_senders": [],
    "cold_outreach_senders": [],
    "suspicious_senders": [],
    "no_reply_domains": [],
    "trusted_human_domains": [],
    "trusted_human_senders": [],
    "subject_patterns": {},
    "body_patterns": {},
    "action_rules": {
        "priority_order": ["NO_REPLY", "CREATE_DRAFT", "REVIEW_MANUALLY"],
        "never_create_draft_if": [],
        "create_draft_if": [],
        "review_manually_if": [],
        "default_action": "REVIEW_MANUALLY",
    },
}

_EMAIL_RE = re.compile(r"<([^>]+)>")


def _extract_email(from_hdr: str) -> str:
    if not from_hdr:
        return ""
    m = _EMAIL_RE.search(from_hdr)
    if m:
        return (m.group(1) or "").strip().lower()
    tokens = re.split(r"\s+", from_hdr.strip())
    for tok in tokens:
        t = tok.strip("<>()[]{}\"',;").lower()
        if "@" in t:
            return t
    return ""


def _domain_of(email: str) -> str:
    if "@" not in (email or ""):
        return ""
    return email.split("@", 1)[1].lower().strip()


def _contains_name_keyword(text: str, keywords_csv: str) -> bool:
    haystack = (text or "")
    kws = [k.strip() for k in (keywords_csv or "").split(",") if k.strip()]
    if not kws:
        return True
    for k in kws:
        if re.search(rf"\b{re.escape(k)}\b", haystack, flags=re.IGNORECASE):
            return True
    return False


def _contains_any_keyword(text: str, keywords_csv: str) -> bool:
    # Backward-compatible alias: some older local copies still call this name.
    return _contains_name_keyword(text, keywords_csv)


def _load_filter_rules(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return DEFAULT_FILTER_RULES
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        print(f"WARNING: Failed to parse filter rules at {path}: {type(ex).__name__}: {ex}")
        return DEFAULT_FILTER_RULES
    if not isinstance(raw, dict):
        return DEFAULT_FILTER_RULES
    merged = dict(DEFAULT_FILTER_RULES)
    merged.update(raw)
    return merged


def _domain_matches(candidate_domain: str, target_domain: str) -> bool:
    c = (candidate_domain or "").lower().strip()
    t = (target_domain or "").lower().strip()
    if not c or not t:
        return False
    return c == t or c.endswith("." + t)


def _matches_pattern_groups(text: str, pattern_groups: dict, group_name: str) -> bool:
    hay = text or ""
    patterns = (pattern_groups or {}).get(group_name) or []
    if not isinstance(patterns, list):
        return False
    for pat in patterns:
        try:
            if re.search(str(pat), hay, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _cond_matches(cond: str, sender_email: str, sender_domain: str, subject: str, body: str, rules: dict) -> bool:
    cond = (cond or "").strip()
    if ":" not in cond:
        return False
    op, arg = cond.split(":", 1)
    op = op.strip()
    arg = arg.strip()

    if op == "sender_in":
        values = {str(x).lower().strip() for x in (rules.get(arg) or [])}
        return sender_email in values

    if op == "domain_in":
        values = [str(x).lower().strip() for x in (rules.get(arg) or [])]
        return any(_domain_matches(sender_domain, v) for v in values)

    if op == "subject_matches":
        return _matches_pattern_groups(subject, rules.get("subject_patterns") or {}, arg)

    if op == "body_matches":
        return _matches_pattern_groups(body, rules.get("body_patterns") or {}, arg)

    return False


def _decide_action(from_hdr: str, subject: str, body: str, thread_context: str, rules: dict) -> tuple[str, str]:
    sender_email = _extract_email(from_hdr)
    sender_domain = _domain_of(sender_email)
    action_rules = (rules.get("action_rules") or {}) if isinstance(rules.get("action_rules"), dict) else {}

    never = [str(x) for x in (action_rules.get("never_create_draft_if") or [])]
    create = [str(x) for x in (action_rules.get("create_draft_if") or [])]
    review = [str(x) for x in (action_rules.get("review_manually_if") or [])]
    default_action = str(action_rules.get("default_action") or "REVIEW_MANUALLY").upper()

    body_with_history = (body or "")
    if (thread_context or "").strip():
        body_with_history = body_with_history + "\n\nTHREAD CONTEXT:\n" + thread_context

    # Safety first: "never create draft" should evaluate only latest inbound email.
    for cond in never:
        if _cond_matches(cond, sender_email, sender_domain, subject, body, rules):
            return "NO_REPLY", cond

    # For create/review rules, consider latest email + thread history.
    for cond in create:
        if _cond_matches(cond, sender_email, sender_domain, subject, body_with_history, rules):
            return "CREATE_DRAFT", cond

    for cond in review:
        if _cond_matches(cond, sender_email, sender_domain, subject, body_with_history, rules):
            return "REVIEW_MANUALLY", cond

    if default_action not in {"NO_REPLY", "CREATE_DRAFT", "REVIEW_MANUALLY"}:
        default_action = "REVIEW_MANUALLY"
    return default_action, "default_action"


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

    filter_rules = _load_filter_rules(s.filter_rules_path)

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

            thread_context = ""
            has_prior_sent = False
            try:
                thread = gmail.get_thread_full(thread_id)
                items = parse_thread(thread)
                thread_context = build_thread_context(items, max_items=6, max_chars=2200)
                has_prior_sent = any(it.is_sent for it in items)
            except Exception:
                thread_context = ""
                has_prior_sent = False

            action, reason = _decide_action(
                from_hdr=from_hdr,
                subject=subject,
                body=body_text,
                thread_context=thread_context,
                rules=filter_rules,
            )

            # If there's ongoing conversation history with outbound messages,
            # prefer drafting a reply instead of manual review.
            if action == "REVIEW_MANUALLY" and has_prior_sent:
                action, reason = "CREATE_DRAFT", "thread_has_prior_sent"

            if action != "CREATE_DRAFT":
                skipped += 1
                print(f"SKIP ({action}:{reason}): {target_id} | {subject[:80]}")
                continue

            if s.require_name_mention:
                hay = f"{subject}\n{body_text}"
                if not _contains_name_keyword(hay, s.name_keywords):
                    skipped += 1
                    print(f"SKIP (no name mention): {target_id} | {subject[:80]}")
                    continue

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
