"""Command line entry point: `data-concierge ask` for one turn, `data-concierge chat` for a
conversation read from standard input. Each turn prints the response JSON of section 9.
`data-concierge audit` prints a turn or a conversation from the audit log (section 8.1)."""

import argparse
import asyncio
import io
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from pydantic import JsonValue

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.audit.models import AuditEvent, AuditEventKind
from data_concierge.audit.service import AuditTrail
from data_concierge.bootstrap import CompositionRoot
from data_concierge.core.settings import Settings


class CommandLine:
    """Runs the parsed command against AskService and writes each response to `stdout`."""

    def __init__(self, service: AskService, stdin: TextIO, stdout: TextIO) -> None:
        self._service = service
        self._stdin = stdin
        self._stdout = stdout

    @staticmethod
    def parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="data-concierge")
        user = argparse.ArgumentParser(add_help=False)
        user.add_argument("--user", type=int, required=True, help="user id passed to procedures")
        commands = parser.add_subparsers(dest="command", required=True)
        ask = commands.add_parser(
            "ask", parents=[user], help="answer one prompt and print the response JSON"
        )
        ask.add_argument("prompt")
        commands.add_parser(
            "chat", parents=[user], help="one conversation, one prompt per line of standard input"
        )
        audit = commands.add_parser(
            "audit", help="print a turn or a conversation from the audit log, step by step"
        )
        audit.add_argument("id", help="correlation id of a turn or session id of a conversation")
        return parser

    async def run(self, args: argparse.Namespace) -> int:
        if args.command == "ask":
            return await self.ask(args.prompt, args.user)
        return await self.chat(args.user)

    async def ask(self, prompt: str, user_id: int) -> int:
        response = await self._service.ask(prompt, user_id)
        self._write(response)
        return 1 if response.status == "error" else 0

    async def chat(self, user_id: int) -> int:
        session_id: str | None = None
        for line in self._stdin:
            prompt = line.strip()
            if not prompt:
                continue
            response = await self._service.ask(prompt, user_id, session_id)
            self._write(response)
            # A dropped session cannot be continued, so the next line starts a new one.
            if self._service.has_session(response.session_id):
                session_id = response.session_id
            else:
                session_id = None
        return 0

    def _write(self, response: Response) -> None:
        self._stdout.write(response.model_dump_json(indent=2) + "\n")
        self._stdout.flush()


class AuditTrailPrinter:
    """Prints the audit events of a turn or a conversation as numbered steps in file order,
    which is the order of the flow: the model's decision, the execution, the result for the
    model. SQL and thinking are printed whole, table rows only as a count."""

    def __init__(self, trail: AuditTrail, stdout: TextIO) -> None:
        self._trail = trail
        self._stdout = stdout
        self._details: dict[AuditEventKind, Callable[[dict[str, JsonValue]], list[str]]] = {
            "request": self._request,
            "model_response": self._model_response,
            "tool_executed": self._tool_executed,
            "model_input": self._model_input,
            "comment": self._comment,
            "turn_failed": self._turn_failed,
            "response": self._response,
        }

    def print(self, turn_or_session_id: str) -> int:
        events = self._trail.events(turn_or_session_id)
        if not events:
            self._write(f"No audit events for {turn_or_session_id} in {self._trail.location}")
            return 1
        turn, step = "", 0
        for event in events:
            if event.correlation_id != turn:
                turn, step = event.correlation_id, 0
                self._write(self._turn_header(event))
            step += 1
            first, *rest = self._details[event.event](event.data) or [""]
            self._write(f"{step:>3}. {event.event:<15} {first}")
            for line in rest:
                self._write(" " * 20 + line)
        return 0

    def _write(self, line: str) -> None:
        self._stdout.write(line + "\n")

    @staticmethod
    def _turn_header(event: AuditEvent) -> str:
        return (
            f"\nTurn {event.correlation_id} (session {event.session_id}, user {event.user_id}), "
            f"{event.ts:%Y-%m-%d %H:%M:%S} UTC"
        )

    @staticmethod
    def _json(value: JsonValue) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _block(text: JsonValue) -> list[str]:
        return [f"  {line}" for line in str(text).splitlines()]

    @staticmethod
    def _list(value: JsonValue) -> list[JsonValue]:
        return value if isinstance(value, list) else []

    @classmethod
    def _dicts(cls, value: JsonValue) -> list[dict[str, JsonValue]]:
        return [item for item in cls._list(value) if isinstance(item, dict)]

    def _request(self, data: dict[str, JsonValue]) -> list[str]:
        session = "continued session" if data["continued_session"] else "new session"
        return [
            f"prompt: {data['prompt']}",
            f"{session}, date {data['current_date']}, mode {data['model_data_access']}, "
            f"model {data['model']}",
        ]

    def _model_response(self, data: dict[str, JsonValue]) -> list[str]:
        lines: list[str] = []
        for thought in self._list(data["thinking"]):
            lines += ["thinking:", *self._block(thought)]
        for text in self._list(data["text"]):
            lines += ["text:", *self._block(text)]
        for call in self._dicts(data["tool_calls"]):
            lines.append(f"call {call['tool']} [{call['tool_call_id']}] {self._json(call['args'])}")
        usage = data["usage"]
        if isinstance(usage, dict):
            lines.append(
                f"tokens: in {usage['input_tokens']}, out {usage['output_tokens']}, "
                f"cache read {usage['cache_read_tokens']}"
            )
        return lines

    def _tool_executed(self, data: dict[str, JsonValue]) -> list[str]:
        head = f"{data['tool']} [{data['tool_call_id']}] {data['duration_ms']} ms"
        if "error" in data:
            lines = [f"{head}: {data['error']}: {data['message']}"]
            if data.get("detail"):
                lines.append(f"detail: {data['detail']}")
            return lines
        truncated = ", truncated" if data["truncated"] else ""
        lines = [f"{head} -> {data['handle']}, {data['rows']} rows{truncated}"]
        lineage = data["lineage"]
        if isinstance(lineage, dict) and lineage["step"] == "procedure":
            lines.append(f"procedure {lineage['procedure']} {self._json(lineage['params'])}")
        elif isinstance(lineage, dict):
            lines.append(f"sources {self._json(lineage['sources'])}, sql:")
            lines += self._block(lineage["submitted_sql"])
        return lines

    def _model_input(self, data: dict[str, JsonValue]) -> list[str]:
        lines: list[str] = []
        for part in self._dicts(data["parts"]):
            call = f"{part['tool']} [{part['tool_call_id']}]"
            content = part["content"]
            if part["kind"] == "retry":
                lines += [f"retry {call}:", *self._block(content)]
            elif isinstance(content, dict) and "handle" in content:
                lines.append(f"result {call}: {content['handle']}, {content['row_count']} rows")
            else:
                lines.append(f"result {call}: {content}")
        return lines

    def _comment(self, data: dict[str, JsonValue]) -> list[str]:
        if "failed" in data:
            return [f"failed, the draft is used: {data['failed']}"]
        return [f"comment: {data['comment']}"]

    def _turn_failed(self, data: dict[str, JsonValue]) -> list[str]:
        return [f"{data['error']}: {data['message']}"]

    def _response(self, data: dict[str, JsonValue]) -> list[str]:
        response = data["response"]
        if not isinstance(response, dict):
            return []
        lines = [
            f"status {response['status']} in {data['duration_ms']} ms",
            f"message: {response['message']}",
        ]
        for table in self._dicts(response.get("tables")):
            lines.append(f"table {table['handle']}: {table['title']} ({table['row_count']} rows)")
        for assumption in self._list(response.get("assumptions")):
            lines.append(f"assumption: {assumption}")
        clarification = response.get("clarification")
        if isinstance(clarification, dict):
            lines.append(f"question: {clarification['question']}")
        return lines


# module-level: the console script entry point declared in pyproject.toml.
def main(argv: Sequence[str] | None = None) -> int:
    args = CommandLine.parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    # Prompts and responses are Polish text; a redirected Windows console would use cp1250.
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    settings = Settings()
    if args.command == "audit":
        return AuditTrailPrinter(CompositionRoot.audit_trail(settings), sys.stdout).print(args.id)
    service = CompositionRoot.from_settings(settings).ask_service
    return asyncio.run(CommandLine(service, sys.stdin, sys.stdout).run(args))
