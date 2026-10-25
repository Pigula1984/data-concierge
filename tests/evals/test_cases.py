"""Each case of evals/cases.yaml through ask() on the real model and database (plan U18, U9).

Run with `uv run pytest -m eval tests/evals`, in the mode of `DC_MODEL_DATA_ACCESS`. A case
fails on a wrong status, a missing procedure call, wrong parameters, a result that differs from
the reference query or wrong clarification candidates; the measures of architecture 002,
section 9 (retries, numbers outside anchors, drafts, time and cost) are only reported.
"""

import logging
from time import perf_counter

import pytest
from cases import CaseChecker, EvalCase
from harness import EvalClock, RecordingModel, RecordingProcedureRepository
from pydantic_ai import capture_run_messages
from report import CaseOutcome, EvalReport, NumberAudit, RunArchive, TurnTrace

from data_concierge.api.schemas import Response
from data_concierge.api.sessions import InMemorySessionRepository
from data_concierge.bootstrap import CompositionRoot
from data_concierge.catalog.models import Catalog
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlServerClient

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

CASES = EvalCase.load_all()
USER_ID = 1
# AskService logs this when the comment phase fails and the turn keeps its draft (plan KTD8).
DRAFT_LOG = "the comment phase failed"


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_case(
    case: EvalCase,
    settings: Settings,
    eval_root: CompositionRoot,
    eval_clock: EvalClock,
    recorder: RecordingProcedureRepository,
    eval_model: RecordingModel,
    eval_sessions: InMemorySessionRepository,
    eval_report: EvalReport,
    run_archive: RunArchive,
    admin_client: SqlServerClient,
    catalog: Catalog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="data_concierge.api.service")
    eval_clock.today = case.today
    recorder.calls.clear()
    eval_model.clear()
    response: Response | None = None
    traces: list[TurnTrace] = []
    seconds: list[float] = []
    numbers: list[str] = []
    prompts = (case.prompt, *case.followups)
    for prompt in prompts:
        session_id = response.session_id if response is not None else None
        started = perf_counter()
        with capture_run_messages() as messages:
            response = await eval_root.ask_service.ask(prompt, USER_ID, session_id)
        seconds.append(perf_counter() - started)
        traces.append(TurnTrace.from_messages(messages))
        session = eval_sessions.get(response.session_id)
        # An error turn leaves the history as it was, with the previous turn's template.
        if response.status != "error" and session is not None:
            numbers += NumberAudit.outside_anchors(session.history)
    assert response is not None

    checker = CaseChecker(catalog)
    expect = case.expectation(settings.model_data_access)
    status_ok = response.status in expect.status
    result_ok = None
    if expect.results and response.status == "ok":
        result_ok = any(
            checker.result_matches(rule, response, admin_client.fetch(rule.sql))
            for rule in expect.results
        )
    candidates_ok = None
    if expect.candidates and response.status == "needs_clarification":
        candidates_ok = checker.candidates_match(expect.candidates, response)
    outcome = CaseOutcome(
        case_id=case.id,
        group=case.group,
        status=response.status,
        status_ok=status_ok,
        procedure_ok=checker.procedures_called(expect.calls, recorder.calls),
        params_ok=checker.params_match(expect.calls, recorder.calls),
        result_ok=result_ok,
        query_retries=sum(trace.query_retries for trace in traces),
        numbers_outside_anchors=tuple(numbers),
        assumptions_ok=(
            checker.assumptions_match(expect.assumptions, response) if expect.assumptions else None
        ),
        candidates_ok=candidates_ok,
        anchor_retries=eval_model.anchor_retries,
        drafts=sum(record.getMessage().startswith(DRAFT_LOG) for record in caplog.records),
        turns=len(prompts),
        seconds=sum(seconds),
        cost=eval_model.cost,
    )
    eval_report.add(outcome)
    saved = run_archive.save_case(
        case.id,
        {
            "id": case.id,
            "group": case.group,
            "mode": settings.model_data_access,
            "today": case.today,
            "turns": [
                {"prompt": prompt, "seconds": round(turn_seconds, 1), "steps": trace.steps}
                for prompt, turn_seconds, trace in zip(prompts, seconds, traces, strict=True)
            ],
            "procedure_calls": [
                {"procedure": procedure, "params": params} for procedure, params in recorder.calls
            ],
            "response": response.model_dump(mode="json"),
            "outcome": {
                "passed": outcome.passed,
                "failed_checks": outcome.failed_checks,
                "numbers_outside_anchors": outcome.numbers_outside_anchors,
                "anchor_retries": outcome.anchor_retries,
                "drafts": outcome.drafts,
                "cost": outcome.cost,
            },
        },
    )

    assert outcome.passed, f"{case.id} failed {outcome.failed_checks}; see {saved}"
