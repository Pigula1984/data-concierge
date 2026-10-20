"""Command line entry point: `data-concierge ask` for one turn, `data-concierge chat` for a
conversation read from standard input. Each turn prints the response JSON of section 9."""

import argparse
import asyncio
import io
import logging
import sys
from collections.abc import Sequence
from typing import TextIO

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
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


# module-level: the console script entry point declared in pyproject.toml.
def main(argv: Sequence[str] | None = None) -> int:
    args = CommandLine.parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    # Prompts and responses are Polish text; a redirected Windows console would use cp1250.
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    service = CompositionRoot.from_settings(Settings()).ask_service
    return asyncio.run(CommandLine(service, sys.stdin, sys.stdout).run(args))
