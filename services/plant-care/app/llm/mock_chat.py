import json
import re

from app.llm.transport import ModelPrompt, TransientModelError
from app.service.model import ModelEffort

_FIRST_SECTION_HEADING = re.compile(r'<kb_section id="S1" [^>]*?heading="([^"]*)"')


class MockChatTransport:
    """A deterministic stand-in for the chat model, for tests and for showing failures.

    `behavior` `unavailable` makes every call fail like a timeout and `malformed` answers with
    something that is not JSON; any other value answers from the first section of the prompt, or
    says that the answer is not in the knowledge base when the prompt has none.
    """

    def __init__(self, behavior: str) -> None:
        self._behavior = behavior

    def complete(
        self, prompt: ModelPrompt, effort: ModelEffort, temperature: float | None = None
    ) -> str:
        if self._behavior == "unavailable":
            raise TransientModelError("mock model is unavailable")
        if self._behavior == "malformed":
            return "this is not json"
        first = _FIRST_SECTION_HEADING.search(prompt.user)
        if first is None:
            return json.dumps(
                {
                    "outcome": "not_in_kb",
                    "answer": "I could not find this in the care guides.",
                    "cited": [],
                }
            )
        return json.dumps(
            {
                "outcome": "answered",
                "answer": f"The care guide section '{first.group(1)}' covers this question.",
                "cited": ["S1"],
            }
        )
