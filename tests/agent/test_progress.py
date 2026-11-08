"""TurnProgress: the steps of a turn as the user sees them."""

import logging

import pytest

from data_concierge.agent.progress import ProgressEvent, TurnProgress


@pytest.fixture
def events() -> list[ProgressEvent]:
    return []


@pytest.fixture
def progress(events: list[ProgressEvent]) -> TurnProgress:
    return TurnProgress(events.append, "c1")


def test_a_procedure_step_names_the_catalog_description_and_the_procedure(
    progress: TurnProgress, events: list[ProgressEvent]
) -> None:
    progress.procedure(
        TurnProgress.procedure_text("Pobieram zamówienia sprzedaży", "api.SalesOrder_List")
    )

    assert events == [
        ProgressEvent(step="procedure", text="Pobieram zamówienia sprzedaży (api.SalesOrder_List)…")
    ]


def test_a_procedure_without_a_description_still_names_the_procedure() -> None:
    assert TurnProgress.procedure_text(None, "api.X") == "Pobieram dane (api.X)…"


def test_the_first_model_round_reads_the_prompt_and_later_ones_the_results(
    progress: TurnProgress, events: list[ProgressEvent]
) -> None:
    progress.model_round()
    progress.query()
    progress.model_round()

    assert [(event.step, event.text) for event in events] == [
        ("model", "Analizuję prośbę…"),
        ("query", "Liczę zestawienie…"),
        ("model", "Analizuję wyniki…"),
    ]


def test_a_round_after_a_correction_reads_the_results(
    progress: TurnProgress, events: list[ProgressEvent]
) -> None:
    progress.model_round()
    progress.retry()
    progress.model_round()

    assert [event.text for event in events] == [
        "Analizuję prośbę…",
        "Poprawiam odpowiedź…",
        "Analizuję wyniki…",
    ]


def test_the_backend_steps_have_fixed_texts(
    progress: TurnProgress, events: list[ProgressEvent]
) -> None:
    progress.guard()
    progress.describe()
    progress.comment()

    assert [(event.step, event.text) for event in events] == [
        ("guard", "Sprawdzam prośbę…"),
        ("describe", "Sprawdzam wynik…"),
        ("comment", "Piszę komentarz…"),
    ]


def test_a_failing_listener_does_not_fail_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    def broken(event: ProgressEvent) -> None:
        raise RuntimeError("closed")

    with caplog.at_level(logging.WARNING, logger="data_concierge"):
        TurnProgress(broken, "c1").guard()

    assert "progress step guard not reported (correlation id c1): closed" in caplog.text


def test_silent_progress_accepts_every_step() -> None:
    progress = TurnProgress.silent()

    progress.guard()
    progress.model_round()
    progress.procedure("Pobieram dane (api.X)…")
