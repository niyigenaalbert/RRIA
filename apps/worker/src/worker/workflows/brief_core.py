"""Shared core of the Daily Revenue Intelligence Brief.

Pure logic and constants only (no SDK imports) so the GitHub Actions entry
point can reuse this module as well as the Mistral workflow.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


def _deny_additional_properties(schema: dict) -> dict:
    """Strict mode requires every object schema to forbid additional properties."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
        for key in ("properties", "$defs", "items"):
            value = schema.get(key)
            if isinstance(value, dict):
                if key in ("$defs", "properties"):
                    for inner in value.values():
                        _deny_additional_properties(inner)
                else:
                    _deny_additional_properties(value)
            elif isinstance(value, list):
                for item in value:
                    _deny_additional_properties(item)
        for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
            for item in schema.get(key) or []:
                _deny_additional_properties(item)
    return schema


def response_format_for(model_class: type[BaseModel]) -> dict:
    """Strict JSON-schema response format accepted by the chat completions endpoint."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_class.__name__.lower(),
            "strict": True,
            "schema": _deny_additional_properties(model_class.model_json_schema()),
        },
    }


async def mistral_conversation(
    api_key: str, model: str, prompt: str, tools: list[dict]
) -> str:
    """One Conversations API call — the only surface that accepts web_search.

    Returns the final assistant message text.
    """
    import httpx

    payload = {"model": model, "inputs": prompt, "store": False, "tools": tools}
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            "https://api.mistral.ai/v1/conversations",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"mistral_conversation failed: HTTP {response.status_code}: {response.text[:300]}"
        )
    outputs = response.json().get("outputs") or []
    for entry in reversed(outputs):
        if entry.get("type") != "message.output":
            continue
        content = entry.get("content")
        if isinstance(content, list):
            content = "".join(
                chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                for chunk in content
            )
        if content:
            return content
    raise RuntimeError("mistral_conversation returned no assistant message")


async def mistral_chat(api_key: str, payload: dict) -> dict:
    """One chat-completions API call over HTTP.

    The `mistralai` SDK's request models reject the server-side `web_search`
    tool, which this brief needs, so the endpoint is called directly. httpx is
    imported here so importing brief_core stays side-effect free.
    """
    import httpx

    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"mistral_chat failed: HTTP {response.status_code}: {response.text[:300]}"
        )
    return response.json()
