"""Daily Revenue Intelligence Brief for Rwanda.

Runs every morning: researches overnight developments from Rwandan institutions
(RRA, MINECOFIN, NISR, BNR, RDB) and the local press, drafts a one-page revenue
intelligence brief in the analyst-approved fixed format, and emails it.
"""

import os
from datetime import timedelta, timezone
from typing import Literal

import mistralai.workflows as workflows
from mistralai.workflows.plugins.mistralai import get_mistral_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError

KIGALI_TZ = timezone(timedelta(hours=2))

DEFAULT_RECIPIENT_EMAIL = "nigalbrt@gmail.com"
RECIPIENT_ENV = "BRIEF_RECIPIENT_EMAIL"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
RESEARCH_MODEL = "mistral-medium-latest"

SECTION_ORDER: tuple[
    tuple[Literal["domestic", "macroeconomic", "international"], str], ...
] = (
    ("domestic", "Domestic Updates"),
    ("macroeconomic", "Macroeconomic Updates"),
    ("international", "International Updates"),
)

RESEARCH_PROMPT = """You are a revenue-intelligence researcher for the Rwanda Revenue Authority \
analyst desk. Today is {date} in Kigali (CAT, UTC+2).

Use web search to collect today's developments that could affect Rwandan tax revenue collection. \
Check the latest news and updates from:
- RRA (rra.gov.rw) - rulings, ministerial orders, VAT law changes, compliance campaigns
- MINECOFIN (minecofin.gov.rw) - budget, fiscal policy, donor financing
- NISR (nisr.gov.rw) - CPI, trade, labour statistics
- BNR (bnr.rw) - monetary policy, exchange rate, bank fees
- RDB (rdb.rw) - investment registrations, manufacturing and export projects
- Rwandan press: The New Times, IGIHE, KT Press, Rwandan Post, business/africa wire

Cover, where evidence exists today or in the last 48 hours: tax law or administrative changes, \
inflation and macro data, fuel and utility prices, floods or droughts affecting agriculture, \
industrial and construction activity, mining incidents, trade disputes, export shocks, IMF or \
World Bank financing, and formalization of digital payments. Some sites block automated access; \
prefer search-indexed headlines and verified secondary coverage over failed direct fetches.

Return concise factual findings. For each finding give: source institution or outlet, what \
changed (with numbers where available), and the tax heads it plausibly affects. Do not invent \
figures; say "unverified" when a claim cannot be confirmed. If nothing new is found for a \
source, skip it."""

DRAFT_PROMPT = """You are a senior revenue analyst. Today is {date}. Convert the research \
findings below into a Daily Revenue Intelligence Brief.

Rules:
- Select only the material changes relevant to tax collection; drop routine items.
- "Change" must state the concrete development with figures where available.
- "Impact" names the affected tax head(s) with a direction arrow, e.g. "VAT ↑".
- "Risk Score" is 1-5, where 4-5 means critical for revenue, 3 means moderate.
- "Forecast" direction values are one of: Positive, Negative, Neutral, or a short qualifier.
- "Actions" must list exactly 3 concrete follow-ups for revenue analysts.
- The executive summary bullets must be one sentence each on the highest-impact changes."""

FINDINGS_HEADER = """Research findings for today (from web search):

{findings}"""


class BriefUpdate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    section: Literal["domestic", "macroeconomic", "international"]
    source: str = Field(description="Institution or outlet, e.g. RRA, NISR, World Bank")
    change: str = Field(
        description="The concrete development, with figures where available"
    )
    impact: str = Field(description="Affected tax head(s) with direction, e.g. 'VAT ↑'")
    risk_score: int = Field(ge=1, le=5, description="1-5; 4-5 critical, 3 moderate")


class RevenueForecast(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    vat: str
    excise: str
    paye: str
    customs: str


class Brief(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    executive_summary: list[str] = Field(
        description="One sentence per highest-impact change"
    )
    updates: list[BriefUpdate] = Field(min_length=1)
    forecast: RevenueForecast
    actions: list[str] = Field(min_length=3, max_length=3)


class BriefInput(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    dry_run: bool = Field(
        default=False,
        description="When true, returns the brief without sending the email",
    )
    recipient: str | None = Field(
        default=None, description="Override the destination email address"
    )


class _FailedSend(workflows.WorkflowError):
    pass


def _separator() -> str:
    return "________________________________________"


def _summary_bullets(updates: list[BriefUpdate]) -> list[str]:
    critical = sum(1 for u in updates if u.risk_score >= 4)
    moderate = sum(1 for u in updates if u.risk_score == 3)
    bullets = []
    if critical:
        bullets.append(
            f"{critical} critical development{'s' if critical != 1 else ''} found."
        )
    if moderate:
        bullets.append(
            f"{moderate} moderate development{'s' if moderate != 1 else ''} found."
        )
    if not bullets:
        bullets.append("No critical or moderate developments found today.")
    return bullets


def render_brief(brief: Brief) -> str:
    lines: list[str] = ["Daily Revenue Intelligence Brief", "", "Executive Summary"]
    lines.extend(f"\u2022 {b}" for b in _summary_bullets(brief.updates))
    number = 0
    for section_key, section_title in SECTION_ORDER:
        section_updates = [u for u in brief.updates if u.section == section_key]
        if not section_updates:
            continue
        lines.append(_separator())
        lines.append(section_title)
        for update in section_updates:
            number += 1
            lines.extend(
                [
                    "",
                    f"Update {number}",
                    f"Source: {update.source}",
                    f"Change: {update.change}",
                    f"Impact: {update.impact}",
                    f"Risk Score: {update.risk_score}/5",
                ]
            )
    lines.append(_separator())
    lines.append("Revenue Forecast Implication")
    lines.append("Expected impact:")
    forecast = brief.forecast
    lines.extend(
        [
            f"\u2022 VAT: {forecast.vat}",
            f"\u2022 Excise: {forecast.excise}",
            f"\u2022 PAYE: {forecast.paye}",
            f"\u2022 Customs: {forecast.customs}",
        ]
    )
    lines.append(_separator())
    lines.append("Action for Revenue Analysts")
    lines.append("Recommended attention areas:")
    lines.extend(f"{i}. {action}" for i, action in enumerate(brief.actions, start=1))
    return "\n".join(lines)


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
    import mistralai.client.models as mistral_models

    client = get_mistral_client()
    response = await client.chat.complete_async(
        model=RESEARCH_MODEL,
        messages=[{"role": "user", "content": RESEARCH_PROMPT.format(date=date)}],
        tools=[mistral_models.WebSearchTool()],
    )
    content = response.choices[0].message.content if response.choices else None
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
    import mistralai.client.models as mistral_models
    from mistralai.extra import response_format_from_pydantic_model

    client = get_mistral_client()
    response = await client.chat.complete_async(
        model=RESEARCH_MODEL,
        messages=[
            {
                "role": "user",
                "content": FINDINGS_HEADER.format(findings=findings)
                + "\n\n"
                + DRAFT_PROMPT.format(date=date),
            }
        ],
        response_format=mistral_models.ResponseFormat.model_validate(
            response_format_from_pydantic_model(Brief)
        ),
    )
    content = response.choices[0].message.content if response.choices else None
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
