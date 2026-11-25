"""Shared core of the Daily Revenue Intelligence Brief.

Pure logic and constants only (no SDK imports) so the GitHub Actions entry
point can reuse this module as well as the Mistral workflow.

Research strategy (no subscription needed): institutional sites and local
press are fetched directly over HTTP, and only plain chat-completions calls
summarize/draft — no Conversations API or Mistral `web_search` tool.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KIGALI_TZ = timezone(timedelta(hours=2))

DEFAULT_RECIPIENT_EMAIL = "nigalbrt@gmail.com"
RECIPIENT_ENV = "BRIEF_RECIPIENT_EMAIL"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
# mistral-small-latest runs on every plan including the free tier; override
# with the MISTRAL_MODEL environment variable for higher-quality drafting
# once a subscription is active.
RESEARCH_MODEL = "mistral-small-latest"


def brief_model() -> str:
    return os.environ.get("MISTRAL_MODEL", "").strip() or RESEARCH_MODEL


SECTION_ORDER: tuple[
    tuple[Literal["domestic", "macroeconomic", "international"], str], ...
] = (
    ("domestic", "Domestic Updates"),
    ("macroeconomic", "Macroeconomic Updates"),
    ("international", "International Updates"),
)

USER_AGENT = "Mozilla/5.0 (compatible; RevenueBrief/1.0; daily revenue digest)"

# (label, url, kind) — kind is one of "html", "rss", "json".
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("RRA", "https://www.rra.gov.rw/", "html"),
    ("MINECOFIN", "https://www.minecofin.gov.rw/news", "html"),
    ("NISR", "https://www.statistics.gov.rw/", "html"),
    ("BNR", "https://www.bnr.rw/news/?lang=en", "json"),
    ("RDB", "https://www.rdb.rw/what-we-do/media", "html"),
    ("The New Times", "https://www.newtimes.co.rw/rss/", "html"),
    ("KT Press", "https://www.ktpress.rw/feed/", "rss"),
)

PER_SOURCE_CHARS = 4000
TOTAL_MATERIAL_CHARS = 48000

MATERIAL_HEADER = "\n\n=== Fetched material (verbatim, per source) ===\n\n"

RESEARCH_PROMPT = """You are a revenue-intelligence researcher for the Rwanda Revenue Authority \
analyst desk. Today is {date} in Kigali (CAT, UTC+2).

Below is raw material fetched this morning directly from Rwandan institutional sites and local \
press feeds: RRA, MINECOFIN, NISR, BNR, RDB, The New Times and KT Press. Sources that failed to \
load are marked, and ordinary site navigation text may appear — ignore it.

Identify the developments in this material from today or the last 48 hours that could affect \
Rwandan tax revenue collection: tax law or administrative changes, VAT matters, inflation and \
macro data, fuel and utility prices, floods or droughts affecting agriculture, industrial and \
construction activity, mining incidents, trade disputes and export shocks, IMF or World Bank \
financing, and formalisation of digital payments.

Return concise factual findings. For each finding give: source institution or outlet, what \
changed (with numbers where available), and the tax heads it plausibly affects. Do not invent \
figures; only use what is present in the material, and say "unverified" when a date or claim \
cannot be confirmed from it. If nothing recent is present in a source, skip that source."""

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

FINDINGS_HEADER = """Research findings for today (from institutional sites and local press):

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
    lines.extend(f"• {b}" for b in _summary_bullets(brief.updates))
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
            f"• VAT: {forecast.vat}",
            f"• Excise: {forecast.excise}",
            f"• PAYE: {forecast.paye}",
            f"• Customs: {forecast.customs}",
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


# --------------------------------------------------------------------------
# Direct source fetching (no Mistral web search required)
# --------------------------------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "noscript"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "section",
        "article",
        "table",
        "figure",
        "header",
        "footer",
        "nav",
    }
)


class _PageReader(HTMLParser):
    """Collects visible text; anchor texts land in the stream on close."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._chunks: list[str] = []
        self._anchor_depth = 0
        self._anchor = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            self._anchor_depth += 1
        elif tag in _BLOCK_TAGS:
            if self._anchor_depth:
                self._flush_anchor()
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            self._flush_anchor()
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._anchor_depth:
            self._anchor = f"{self._anchor} {text}".strip()
        else:
            self._chunks.append(text + " ")

    def _flush_anchor(self) -> None:
        self._anchor_depth = max(0, self._anchor_depth - 1)
        if self._anchor:
            self._chunks.append(self._anchor + "\n")
        self._anchor = ""


def html_to_text(markup: str, max_chars: int = PER_SOURCE_CHARS) -> str:
    """Strip markup to visible text, one line per element."""
    reader = _PageReader()
    reader.feed(markup)
    reader.close()
    lines = [line.strip() for line in "".join(reader._chunks).splitlines()]
    return "\n".join(line for line in lines if line)[:max_chars]


def rss_headlines(xml: str, limit: int = 25) -> str:
    """Extract title/link/pubDate from RSS 2.0 or Atom feeds without a parser lib."""
    import html as htmllib
    import re

    items = re.findall(r"<item[ >].*?</item>|<entry[ >].*?</entry>", xml, re.S)
    lines: list[str] = []
    for item in items[:limit]:
        title = re.search(r"<title[^>]*>(.*?)</title>", item, re.S)
        link = re.search(
            r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>|"
            r"<link[^>]*href=['\"]([^'\"]+)['\"]",
            item,
            re.S,
        )
        pub = re.search(
            r"<pubDate[^>]*>(.*?)</pubDate>|<updated[^>]*>(.*?)</updated>",
            item,
            re.S,
        )
        if not title:
            continue
        title_text = htmllib.unescape(
            re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1))
        ).strip()
        line = f"- {title_text}"
        if link:
            url = (link.group(1) or link.group(2) or "").strip()
            if url:
                line = f"{line} ({url})"
        if pub:
            date_text = (pub.group(1) or pub.group(2) or "").strip()
            if date_text:
                line = f"{line} [{date_text}]"
        lines.append(line)
    return "\n".join(lines)[:PER_SOURCE_CHARS]


def _json_items(raw: str) -> str:
    """BNR exposes its news list as JSON; pull titles and optional dates."""
    import json

    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:PER_SOURCE_CHARS]
    if isinstance(data, dict):
        data = data.get("data") or data.get("items") or []
    if not isinstance(data, list):
        return raw[:PER_SOURCE_CHARS]
    lines = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name")
        if not title:
            continue
        date = item.get("date") or item.get("created") or item.get("published_at")
        lines.append(f"- {title} [{date}]" if date else f"- {title}")
    return "\n".join(lines)[:PER_SOURCE_CHARS]


async def fetch_material() -> str:
    """Fetch every configured source concurrently; failures become a marker line."""
    import asyncio
    import warnings

    import httpx

    async def fetch_source(
        client: "httpx.AsyncClient", label: str, url: str, kind: str
    ) -> str:
        try:
            response = await client.get(
                url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}
            )
        except Exception as error:  # noqa: BLE001 - one bad site must not kill the run
            return f"== {label} ==\n[fetch failed: {type(error).__name__}]"
        if response.status_code != 200 or not response.text:
            return f"== {label} ==\n[fetch failed: HTTP {response.status_code}]"
        if kind == "rss":
            body = rss_headlines(response.text)
            if not body.strip():
                body = html_to_text(response.text)
        elif kind == "json":
            body = _json_items(response.text)
        else:
            body = html_to_text(response.text)
        if not body.strip():
            body = "[no readable content]"
        return f"== {label} ==\n{body}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # some .gov.rw certificates are incomplete
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, verify=False
        ) as client:
            blocks = await asyncio.gather(
                *(
                    fetch_source(client, label, url, kind)
                    for label, url, kind in SOURCES
                )
            )
    material = "\n\n".join(blocks)
    return material[:TOTAL_MATERIAL_CHARS]


async def research_findings(api_key: str, date: str) -> str:
    """Fetch sources, then summarize them via a plain chat-completions call."""
    material = await fetch_material()
    payload = {
        "model": brief_model(),
        "messages": [
            {
                "role": "user",
                "content": RESEARCH_PROMPT.format(date=date)
                + MATERIAL_HEADER
                + material,
            }
        ],
        "max_tokens": 4000,
    }
    data = await mistral_chat(api_key, payload)
    content = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
    if not content:
        raise RuntimeError("research returned no content")
    return content


async def mistral_chat(api_key: str, payload: dict) -> dict:
    """One chat-completions API call over HTTP.

    The `mistralai` SDK's request models reject the server-side tools used by
    agent surfaces, so the endpoint is called directly. httpx is imported here
    so importing brief_core stays side-effect free.
    """
    import httpx

    async with httpx.AsyncClient(timeout=240) as client:
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
