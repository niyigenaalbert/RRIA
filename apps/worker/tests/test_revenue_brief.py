"""Tests for the Daily Revenue Intelligence Brief workflow.

External effects (source fetching, Mistral chat completions, Gmail SMTP) are
mocked; the deterministic orchestration and rendered output are asserted.
"""

import asyncio
import smtplib
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from mistralai.workflows.testing import create_test_worker

from worker.workflows import revenue_brief as rb
from worker.workflows import brief_core
from worker.workflows.revenue_brief import (
    Brief,
    BriefUpdate,
    RevenueForecast,
    RevenueIntelligenceBrief,
    render_brief,
)

WORKFLOW_EXECUTION_TIMEOUT = timedelta(seconds=10)

SAMPLE_BRIEF = Brief(
    executive_summary=[
        "RRA overhaul of VAT refund handling lifts import VAT.",
        "October CPI jump pressures real consumption.",
    ],
    updates=[
        BriefUpdate(
            section="domestic",
            source="RRA",
            change="VAT Deferral Facility replaces exemption on imported raw materials.",
            impact="Import VAT ↑",
            risk_score=5,
        ),
        BriefUpdate(
            section="macroeconomic",
            source="NISR",
            change="Urban CPI rose from 17.2% to 19.5%.",
            impact="VAT and Excise ↑",
            risk_score=4,
        ),
        BriefUpdate(
            section="international",
            source="IMF",
            change="ECF first review of about $35.7m pending.",
            impact="Donor-financed spending",
            risk_score=3,
        ),
    ],
    forecast=RevenueForecast(
        vat="Positive", excise="Positive", paye="Neutral", customs="Positive"
    ),
    actions=[
        "Track VAT refund retention.",
        "Model real vs nominal VAT gap.",
        "Watch export distress.",
    ],
)


def _mock_mistral(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"choices": [{"message": {"content": SAMPLE_BRIEF.model_dump_json()}}]}
    monkeypatch.setattr(
        rb,
        "research_findings",
        AsyncMock(return_value="RRA: VAT update. NISR: CPI up 2 points."),
    )
    monkeypatch.setattr(rb, "mistral_chat", AsyncMock(return_value=payload))
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")


async def test_research_findings_wires_material_into_prompt(monkeypatch) -> None:
    monkeypatch.setattr(
        brief_core, "fetch_material", AsyncMock(return_value="== RRA ==\nheadline one")
    )
    captured = {}

    async def fake_chat(api_key: str, payload: dict) -> dict:
        captured["api_key"] = api_key
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "findings text"}}]}

    monkeypatch.setattr(brief_core, "mistral_chat", fake_chat)
    monkeypatch.delenv("MISTRAL_MODEL", raising=False)

    out = await brief_core.research_findings("key-123", "Monday 12 May 2026")

    assert out == "findings text"
    assert captured["api_key"] == "key-123"
    content = captured["payload"]["messages"][0]["content"]
    assert "Monday 12 May 2026" in content
    assert "== RRA ==\nheadline one" in content
    assert captured["payload"]["model"] == brief_core.RESEARCH_MODEL
    assert "tools" not in captured["payload"]


def test_html_to_text_strips_markup() -> None:
    markup = (
        "<html><head><title>News</title></head><body>"
        "<script>var tracked = 1;</script>"
        "<nav><a href='/menu'>Menu item filler</a></nav>"
        "<a href='/n1'>Corridor upgrade boosts trade</a>"
        "</body></html>"
    )
    text = brief_core.html_to_text(markup)
    assert "Corridor upgrade boosts trade" in text
    assert "var tracked" not in text


def test_rss_headlines_extracts_titles() -> None:
    xml = (
        "<rss><channel>"
        "<item><title>Kigali port volumes rise</title>"
        "<link><![CDATA[https://ktpress.example/post-1]]></link>"
        "<pubDate>Thu, 14 May 2026 07:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    out = brief_core.rss_headlines(xml)
    assert "- Kigali port volumes rise (https://ktpress.example/post-1)" in out
    assert "[Thu, 14 May 2026" in out


async def test_dry_run_renders_brief_without_email(temporal_env, monkeypatch) -> None:
    _mock_mistral(monkeypatch)
    async with create_test_worker(
        temporal_env,
        workflows=[RevenueIntelligenceBrief],
        activities=[
            rb.current_kigali_date,
            rb.collect_intelligence,
            rb.draft_brief,
            rb.send_email,
        ],
    ):
        handle = await temporal_env.client.start_workflow(
            "revenue-intelligence-brief",
            {"dry_run": True},
            id="test-brief-dry-run",
            task_queue="test-task-queue",
            execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
        )
        result = await asyncio.wait_for(handle.result(), timeout=15)

    rendered = result["result"]
    assert "Sent to:" not in rendered
    assert "Daily Revenue Intelligence Brief" in rendered
    assert "Source: RRA" in rendered
    assert "Risk Score: 5/5" in rendered


def test_render_matches_brief_format() -> None:
    rendered = render_brief(SAMPLE_BRIEF)
    assert rendered.startswith("Daily Revenue Intelligence Brief")
    assert "Executive Summary" in rendered
    assert "2 critical developments found." in rendered
    assert "1 moderate development found." in rendered
    assert "Domestic Updates" in rendered
    assert "Macroeconomic Updates" in rendered
    assert "International Updates" in rendered
    assert "Update 3" in rendered
    assert "\u2022 VAT: Positive" in rendered
    assert "Recommended attention areas:" in rendered
    assert "1. Track VAT refund retention." in rendered


def test_render_handles_no_critical() -> None:
    quiet = SAMPLE_BRIEF.model_copy(
        update={
            "updates": [
                u.model_copy(update={"risk_score": 2}) for u in SAMPLE_BRIEF.updates
            ]
        }
    )
    assert "No critical or moderate developments found today." in render_brief(quiet)


async def test_send_email_uses_smtp_with_secret(temporal_env, monkeypatch) -> None:
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-secret-value")
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            captured["user"] = user
            captured["password"] = password

        def send_message(self, message):
            captured["to"] = message["To"]
            captured["subject"] = message["Subject"]

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    to_address = await asyncio.wait_for(
        rb.send_email("test@example.com", "Brief Subject", "Body line"), 5
    )
    assert to_address == "test@example.com"
    assert captured["host"] == rb.SMTP_HOST
    assert captured["password"] == "test-secret-value"
    assert captured["subject"] == "Brief Subject"


async def test_send_email_requires_secret(temporal_env, monkeypatch) -> None:
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    with pytest.raises(rb.workflows.WorkflowError):
        await asyncio.wait_for(
            rb.send_email("test@example.com", "Brief Subject", "Body line"), 5
        )
