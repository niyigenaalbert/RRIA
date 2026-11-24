"""Daily Revenue Intelligence Brief for Rwanda.

Runs every morning: researches overnight developments from Rwandan institutions
(RRA, MINECOFIN, NISR, BNR, RDB) and the local press, drafts a one-page revenue
intelligence brief in the analyst-approved fixed format, and emails it.
"""

import os
from datetime import timedelta

import mistralai.workflows as workflows
from pydantic import ValidationError

from worker.workflows.brief_core import (  # noqa: F401  (re-exported for tests)
    DEFAULT_RECIPIENT_EMAIL,
    RECIPIENT_ENV,
    RESEARCH_MODEL,
    SECTION_ORDER,
    SMTP_HOST,
    SMTP_PORT,
    Brief,
    BriefInput,
    BriefUpdate,
    DRAFT_PROMPT,
    FINDINGS_HEADER,
    KIGALI_TZ,
    RESEARCH_PROMPT,
    mistral_chat,
    response_format_for,
    RevenueForecast,
    _separator,
    _summary_bullets,
    render_brief,
)


class _FailedSend(workflows.WorkflowError):
    pass


@workflows.activity(
    name="brief-current-kigali-date",
    start_to_close_timeout=timedelta(seconds=10),
    retry_policy_max_attempts=1,
)
async def current_kigali_date() -> str:
    from datetime import datetime

    return datetime.now(KIGALI_TZ).strftime("%A %d %B %Y")


@workflows.activity(
    name="brief-collect-intelligence",
    start_to_close_timeout=timedelta(seconds=300),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def collect_intelligence(date: str) -> str:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise workflows.WorkflowError(
            "mistral_api_key_not_configured", non_retryable=True
        )
    payload = {
        "model": RESEARCH_MODEL,
        "messages": [{"role": "user", "content": RESEARCH_PROMPT.format(date=date)}],
        "tools": [{"type": "web_search"}],
    }
    data = await mistral_chat(api_key, payload)
    content = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
    if not content:
        raise workflows.WorkflowError(
            "research_returned_no_content", non_retryable=True
        )
    return content


@workflows.activity(
    name="brief-draft-brief",
    start_to_close_timeout=timedelta(seconds=240),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def draft_brief(findings: str, date: str) -> Brief:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise workflows.WorkflowError(
            "mistral_api_key_not_configured", non_retryable=True
        )
    payload = {
        "model": RESEARCH_MODEL,
        "messages": [
            {
                "role": "user",
                "content": FINDINGS_HEADER.format(findings=findings)
                + "\n\n"
                + DRAFT_PROMPT.format(date=date),
            }
        ],
        "response_format": response_format_for(Brief),
    }
    data = await mistral_chat(api_key, payload)
    content = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
    if not content:
        raise workflows.WorkflowError("draft_returned_no_content", non_retryable=True)
    try:
        return Brief.model_validate_json(content)
    except ValidationError:
        raise workflows.WorkflowError(
            "draft_failed_validation", non_retryable=True
        ) from None


@workflows.activity(
    name="brief-send-email",
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy_max_attempts=2,
    retry_policy_backoff_coefficient=2.0,
)
async def send_email(recipient: str | None, subject: str, body_text: str) -> str:
    import asyncio
    import smtplib
    from email.message import EmailMessage
    from email.utils import formataddr

    to_address = (
        recipient or os.environ.get(RECIPIENT_ENV, "") or DEFAULT_RECIPIENT_EMAIL
    )
    from_address = os.environ.get("GMAIL_FROM_USER", "") or DEFAULT_RECIPIENT_EMAIL
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        raise workflows.WorkflowError(
            "gmail_app_password_not_configured",
            non_retryable=True,
        )

    message = EmailMessage()
    message["From"] = formataddr(("Revenue Intelligence Agent", from_address))
    message["To"] = to_address
    message["Subject"] = subject
    escaped = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    message.set_content(body_text)
    message.add_alternative(
        '<html><body><pre style="font-family:Arial,sans-serif;font-size:13px;'
        'white-space:pre-wrap">'
        f"{escaped}"
        "</pre></body></html>",
        subtype="html",
    )

    def _deliver() -> None:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(from_address, password)
            server.send_message(message)

    try:
        await asyncio.to_thread(_deliver)
    except smtplib.SMTPAuthenticationError:
        raise _FailedSend("gmail_auth_failed", non_retryable=True) from None
    except (smtplib.SMTPException, TimeoutError, OSError):
        raise workflows.WorkflowError("gmail_delivery_failed") from None
    return to_address


@workflows.workflow.define(
    name="revenue-intelligence-brief",
    workflow_display_name="Daily Revenue Intelligence Brief",
    workflow_description=(
        "Researches Rwanda revenue-relevant developments and emails a one-page brief."
    ),
)
class RevenueIntelligenceBrief:
    @workflows.workflow.entrypoint
    async def run(self, data: BriefInput) -> str:
        date = await current_kigali_date()
        findings = await collect_intelligence(date)
        brief = await draft_brief(findings, date)
        rendered = render_brief(brief)
        if not data.dry_run:
            to_address = await send_email(
                data.recipient,
                f"Daily Revenue Intelligence Brief \u2013 {date}",
                rendered,
            )
            rendered += f"\n\nSent to: {to_address}"
        return rendered
