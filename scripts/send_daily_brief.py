"""Standalone Daily Revenue Intelligence Brief entry point for GitHub Actions.

Mirrors the Mistral workflow (`apps/worker/src/worker/workflows/revenue_brief.py`)
using the shared core in `brief_core.py`: research via Mistral web search, draft
into the fixed brief format, deliver via Gmail SMTP.

Required environment:
- MISTRAL_API_KEY: Mistral API key (Models/Chat + web search are billed per use)
- GMAIL_APP_PASSWORD: Gmail app password of the sending account (2-Step Verification)
Optional environment:
- BRIEF_RECIPIENT_EMAIL: destination address (default: the coded default)
- GMAIL_FROM_USER: sending address (default: the coded default)

Usage: python scripts/send_daily_brief.py [--dry-run] [--recipient someone@example.com]
"""

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "worker" / "src"))

from worker.workflows.brief_core import (  # noqa: E402
    DEFAULT_RECIPIENT_EMAIL,
    DRAFT_PROMPT,
    FINDINGS_HEADER,
    KIGALI_TZ,
    RECIPIENT_ENV,
    RESEARCH_MODEL,
    RESEARCH_PROMPT,
    Brief,
    render_brief,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _research(client, date: str) -> str:
    import mistralai.client.models as mistral_models

    response = client.chat.complete(
        model=RESEARCH_MODEL,
        messages=[{"role": "user", "content": RESEARCH_PROMPT.format(date=date)}],
        tools=[mistral_models.WebSearchTool()],
    )
    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise SystemExit("Research step returned no content")
    return content


def _draft(client, findings: str, date: str) -> Brief:
    response = client.chat.parse(
        model=RESEARCH_MODEL,
        response_format=Brief,
        messages=[
            {
                "role": "user",
                "content": FINDINGS_HEADER.format(findings=findings)
                + "\n\n"
                + DRAFT_PROMPT.format(date=date),
            }
        ],
    )
    parsed = response.choices[0].message.parsed if response.choices else None
    if not isinstance(parsed, Brief):
        raise SystemExit("Draft step did not return a valid Brief")
    return parsed


def _send(recipient: str, sender: str, password: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr(("Revenue Intelligence Agent", sender))
    message["To"] = recipient
    message["Subject"] = subject
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    message.set_content(body)
    message.add_alternative(
        '<html><body><pre style="font-family:Arial,sans-serif;font-size:13px;'
        'white-space:pre-wrap">'
        f"{escaped}"
        "</pre></body></html>",
        subtype="html",
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, password)
        server.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the brief without emailing"
    )
    parser.add_argument("--recipient", help="Override the destination email address")
    args = parser.parse_args()

    api_key = _required("MISTRAL_API_KEY")
    recipient = (
        args.recipient or os.environ.get(RECIPIENT_ENV, "") or DEFAULT_RECIPIENT_EMAIL
    )
    sender = os.environ.get("GMAIL_FROM_USER", "") or DEFAULT_RECIPIENT_EMAIL

    date = datetime.now(KIGALI_TZ).strftime("%A %d %B %Y")

    from mistralai.client import Mistral

    client = Mistral(api_key=api_key)
    findings = _research(client, date)
    brief = _draft(client, findings, date)
    rendered = render_brief(brief)
    print(rendered)

    if args.dry_run:
        print("\n[dry-run] email not sent", file=sys.stderr)
        return

    password = _required("GMAIL_APP_PASSWORD")
    _send(
        recipient,
        sender,
        password,
        f"Daily Revenue Intelligence Brief \u2013 {date}",
        rendered,
    )
    print(f"\nSent to: {recipient}", file=sys.stderr)


if __name__ == "__main__":
    main()
