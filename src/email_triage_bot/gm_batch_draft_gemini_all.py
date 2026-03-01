from __future__ import annotations

import argparse
import subprocess
import sys

from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.profiles import load_profiles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=str, default="", help="Comma-separated profile names. If empty, runs all profiles in profiles.json.")
    ap.add_argument("--limit", type=int, default=None, help="Override BATCH_LIMIT for each profile run")
    ap.add_argument("--query", type=str, default=None, help="Override GMAIL_QUERY for each profile run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-mark-read", action="store_true")
    args = ap.parse_args()

    s = Settings()
    setup_logging(s.log_level)

    profiles = load_profiles(s.profiles_path)
    if not profiles:
        raise SystemExit(f"No profiles found. Check PROFILES_PATH={s.profiles_path}")

    if args.profiles.strip():
        names = [p.strip() for p in args.profiles.split(",") if p.strip()]
    else:
        names = list(profiles.keys())

    exe = sys.executable

    any_fail = False

    for name in names:
        print("\n" + "=" * 70)
        print(f"RUN PROFILE: {name}")
        print("=" * 70)

        cmd = [exe, "-m", "email_triage_bot.gm_batch_draft_gemini", "--profile", name]
        if args.limit is not None:
            cmd += ["--limit", str(args.limit)]
        if args.query is not None:
            cmd += ["--query", str(args.query)]
        if args.dry_run:
            cmd += ["--dry-run"]
        if args.no_mark_read:
            cmd += ["--no-mark-read"]

        res = subprocess.run(cmd)
        if res.returncode != 0:
            any_fail = True
            print(f"[ERROR] Profile '{name}' failed with exit code {res.returncode}")

    print("\nAll profiles done.")
    if any_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
