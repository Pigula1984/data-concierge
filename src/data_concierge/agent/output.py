"""OutputPolicy: links in the texts the model writes (architecture, section 8.2)."""

import re
from collections.abc import Iterable
from typing import Literal

from pydantic_ai import ModelRetry

from data_concierge.audit.service import TurnAudit

type OutputPhase = Literal["tools", "comment"]

# Any `://`, because what stands before it may be a hiding prefix or an anchor with the scheme,
# and `www.` after anything but a letter: those are the links a reader can click. A bare domain
# would be confused with plain text. The prefix is bounded to keep the search linear.
LINK = re.compile(r"\S{0,32}://\S*|(?<![^\W\d_])www\.\S+", re.IGNORECASE)


class OutputPolicy:
    """Sends a text with a link back to the model, like a wrong anchor. It reads the template
    before the anchors are filled, so a value from the data is never checked: a link in a text
    is what the model chose to write, and a value is what the user asked to see."""

    def __init__(self, audit: TurnAudit) -> None:
        self._audit = audit

    def check(self, texts: Iterable[str], phase: OutputPhase) -> None:
        urls = [url for text in texts for url in LINK.findall(text)]
        if urls:
            self._audit.record("output_blocked", {"reason": "url", "phase": phase, "urls": [*urls]})
            raise ModelRetry(f"texts for the user cannot hold links, remove: {', '.join(urls)}")
