"""OutputPolicy: links in the texts the model writes (architecture, section 8.2)."""

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import JsonValue
from pydantic_ai import ModelRetry

from data_concierge.audit.service import TurnAudit

type OutputPhase = Literal["tools", "comment"]

# A scheme with `://` or `www.`: those are the links a reader can click. A bare domain would be
# confused with plain text.
LINK = re.compile(r"\b[a-z][a-z0-9+.-]*://\S*|\bwww\.\S+", re.IGNORECASE)


class OutputPolicy:
    """Sends a text with a link back to the model, like a wrong anchor. It reads the template
    before the anchors are filled, so a value from the data is never checked: a link in a text
    is what the model chose to write, and a value is what the user asked to see."""

    @staticmethod
    def check(texts: Iterable[str], phase: OutputPhase, audit: TurnAudit) -> None:
        urls: list[JsonValue] = [url for text in texts for url in LINK.findall(text)]
        if urls:
            audit.record("output_blocked", {"reason": "url", "phase": phase, "urls": urls})
            raise ModelRetry(
                f"texts for the user cannot hold links, remove: {', '.join(map(str, urls))}"
            )
