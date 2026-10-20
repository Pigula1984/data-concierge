"""Each case of evals/cases.yaml through ask() on the real model and database (plan U18).

Run with `uv run pytest -m eval tests/evals`. A case fails on a wrong status, a missing
procedure call, wrong parameters or a result that differs from the reference query; numbers
in the comment that no result contains are only reported.
"""

import pytest
from cases import CaseChecker, EvalCase
from harness import EvalClock, RecordingProcedureRepository
from pydantic_ai import capture_run_messages
from report import CaseOutcome, EvalReport, NumberAudit, RunArchive, TurnTrace

from data_concierge.api.schemas import Response
from data_concierge.bootstrap import CompositionRoot
from data_concierge.catalog.models import Catalog
from data_concierge.core.sqlserver import SqlServerClient

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

CASES = EvalCase.load_all()
USER_ID = 1


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_case(
    case: EvalCase,
    eval_root: CompositionRoot,
    eval_clock: EvalClock,
    recorder: RecordingProcedureRepository,
    eval_report: EvalReport,
    run_archive: RunArchive,
    admin_client: SqlServerClient,
    catalog: Catalog,
) -> None:
    eval_clock.today = case.today
    recorder.calls.clear()
    response: Response | None = None
    traces: list[TurnTrace] = []
    prompts = (case.prompt, *case.followups)
    for prompt in prompts:
        session_id = response.session_id if response is not None else None
        with capture_run_messages() as messages:
            response = await eval_root.ask_service.ask(prompt, USER_ID, session_id)
        traces.append(TurnTrace.from_messages(messages))
    assert response is not None

    checker = CaseChecker(catalog)
    expect = case.expect
    status_ok = response.status in expect.status
    result_ok = None
    if expect.results and response.status == "ok":
        result_ok = any(
            checker.result_matches(rule, response, admin_client.fetch(rule.sql))
            for rule in expect.results
        )
    outcome = CaseOutcome(
        case_id=case.id,
        group=case.group,
        status=response.status,
        status_ok=status_ok,
        procedure_ok=checker.procedures_called(expect.calls, recorder.calls),
        params_ok=checker.params_match(expect.calls, recorder.calls),
        result_ok=result_ok,
        query_retries=sum(trace.query_retries for trace in traces),
        assumptions_ok=(
            checker.assumptions_match(expect.assumptions, response) if expect.assumptions else None
        ),
        unmatched_numbers=NumberAudit.unmatched(
            response, [value for trace in traces for value in trace.seen_values]
        ),
    )
    eval_report.add(outcome)
    saved = run_archive.save_case(
        case.id,
        {
            "id": case.id,
            "group": case.group,
            "today": case.today,
            "turns": [
                {"prompt": prompt, "steps": trace.steps}
                for prompt, trace in zip(prompts, traces, strict=True)
            ],
            "procedure_calls": [
                {"procedure": procedure, "params": params} for procedure, params in recorder.calls
            ],
            "response": response.model_dump(mode="json"),
            "outcome": {"passed": outcome.passed, "failed_checks": outcome.failed_checks},
        },
    )

    assert outcome.passed, f"{case.id} failed {outcome.failed_checks}; see {saved}"
