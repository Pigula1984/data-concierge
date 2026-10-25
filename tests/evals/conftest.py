"""The evaluation set runs through ask() on the real model and database (plan U18, KTD5).

The application is built once per run with the composition root, over a procedure repository
that records every call, a model that records every response, a session repository the cases
read the history from, and a clock each case sets to its own "today". The mode is the setting
`DC_MODEL_DATA_ACCESS` (plan KTD11). The report by group is printed after the run; every case
and the report are kept in evals/runs/<start of the run>-<mode>.
"""

from datetime import date
from pathlib import Path

import pytest
from harness import EvalClock, RecordingModel, RecordingProcedureRepository
from report import EvalReport, RunArchive

from data_concierge.api.sessions import InMemorySessionRepository
from data_concierge.bootstrap import CompositionRoot
from data_concierge.catalog.repository import SqlServerMetadataRepository
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlServerClient
from data_concierge.procedures.repository import SqlServerProcedureRepository

REPORT_KEY = pytest.StashKey[EvalReport]()
ARCHIVE_KEY = pytest.StashKey[RunArchive]()
RUNS_PATH = Path(__file__).parents[2] / "evals" / "runs"


@pytest.fixture(scope="session")
def eval_clock() -> EvalClock:
    return EvalClock(date.today())


@pytest.fixture(scope="session")
def recorder(agent_client: SqlServerClient) -> RecordingProcedureRepository:
    return RecordingProcedureRepository(SqlServerProcedureRepository(agent_client))


@pytest.fixture(scope="session")
def eval_model(settings: Settings) -> RecordingModel:
    if settings.anthropic_api_key is None:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    return RecordingModel(CompositionRoot.configured_model(settings))


@pytest.fixture(scope="session")
def eval_sessions() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture(scope="session")
def eval_root(
    settings: Settings,
    agent_client: SqlServerClient,
    recorder: RecordingProcedureRepository,
    eval_model: RecordingModel,
    eval_sessions: InMemorySessionRepository,
    eval_clock: EvalClock,
) -> CompositionRoot:
    return CompositionRoot.assemble(
        settings,
        SqlServerMetadataRepository(agent_client),
        recorder,
        eval_model,
        today=eval_clock,
        sessions=eval_sessions,
    )


@pytest.fixture(scope="session")
def eval_report(request: pytest.FixtureRequest, settings: Settings) -> EvalReport:
    report = EvalReport(settings.model_data_access)
    request.config.stash[REPORT_KEY] = report
    return report


@pytest.fixture(scope="session")
def run_archive(request: pytest.FixtureRequest, settings: Settings) -> RunArchive:
    archive = RunArchive.started_now(RUNS_PATH, settings.model_data_access)
    request.config.stash[ARCHIVE_KEY] = archive
    return archive


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    report = config.stash.get(REPORT_KEY, None)
    if report is not None:
        terminalreporter.section("evaluation report")
        terminalreporter.write_line(report.render())
    archive = config.stash.get(ARCHIVE_KEY, None)
    if report is not None and archive is not None:
        path = archive.save_report(report.render())
        terminalreporter.write_line(f"Run archive: {path.parent}")
