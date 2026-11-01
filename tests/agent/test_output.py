"""OutputPolicy: links in the texts the model writes go back to the model for a fix."""

from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from data_concierge.agent.output import OutputPolicy
from data_concierge.audit.repository import JsonlAuditLog, NullAuditLog
from data_concierge.audit.service import TurnAudit
from data_concierge.core.context import RequestContext

CONTEXT = RequestContext(user_id=7, role="user")
AUDIT = TurnAudit(NullAuditLog(), CONTEXT, "s1")


@pytest.mark.parametrize(
    "text",
    [
        "Więcej na https://example.test/?q={r1[1].CustomerName}",
        "Szczegóły: http://example.test",
        "ftp://example.test/plik",
        "HTTPS://EXAMPLE.TEST",
        "Zobacz www.example.test",
    ],
)
def test_a_link_goes_back_to_the_model(text: str) -> None:
    with pytest.raises(ModelRetry, match=r"(?i)example"):
        OutputPolicy.check(["Bez linku.", text], "tools", AUDIT)


@pytest.mark.parametrize(
    "text",
    [
        "Sprzedaż netto: {r1.net_sales}.",
        "Klient Sp. z o.o. z Berlina.",
        "Stosunek 3:1 i wartość {r2[1].net_sales}.",
        "Okres: od 2024-01-01 do 2024-12-31.",
        "awww.nie-jest-adresem",
    ],
)
def test_plain_text_and_anchors_pass(text: str) -> None:
    OutputPolicy.check([text], "tools", AUDIT)


def test_a_rejected_link_is_audited_with_its_phase(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    audit = TurnAudit(log, CONTEXT, "s1")

    with pytest.raises(ModelRetry):
        OutputPolicy.check(["https://example.test"], "comment", audit)

    [event] = log.events("s1")
    assert event.event == "output_blocked"
    assert event.data == {"reason": "url", "phase": "comment", "urls": ["https://example.test"]}
