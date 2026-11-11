import logging
import time

from botocore.exceptions import BotoCoreError, ClientError

from app.llm.transport import ModelPrompt, PermanentModelError, TransientModelError
from app.service.model import ModelEffort

logger = logging.getLogger(__name__)

TRANSIENT_CODES = frozenset(
    {
        "ThrottlingException",
        "ServiceUnavailableException",
        "ModelTimeoutException",
        "InternalServerException",
        "ModelNotReadyException",
    }
)
IMAGE_FORMATS = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class BedrockTransport:
    """Amazon Bedrock Converse API; credentials come from the default AWS credential chain."""

    def __init__(
        self,
        client,
        model_id: str,
        max_output_tokens: int,
        thinking_budgets: dict[ModelEffort, int],
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._thinking_budgets = thinking_budgets

    def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
        started = time.monotonic()
        budget = self._thinking_budgets[effort]
        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": prompt.system}],
                messages=[{"role": "user", "content": self._content(prompt)}],
                inferenceConfig={"maxTokens": budget + self._max_output_tokens},
                additionalModelRequestFields={
                    "thinking": {"type": "enabled", "budget_tokens": budget}
                },
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            self._log(started, outcome=code)
            if code in TRANSIENT_CODES:
                raise TransientModelError(code) from error
            raise PermanentModelError(code) from error
        except BotoCoreError as error:
            self._log(started, outcome=type(error).__name__)
            raise TransientModelError from error
        self._log(started, outcome="ok")
        return self._text_of(response)

    @staticmethod
    def _content(prompt: ModelPrompt) -> list[dict]:
        content: list[dict] = [{"text": prompt.user}]
        for image in prompt.images:
            image_format = IMAGE_FORMATS.get(image.media_type)
            if image_format is None:
                raise PermanentModelError("unsupported image type")
            content.append({"image": {"format": image_format, "source": {"bytes": image.data}}})
        return content

    @staticmethod
    def _text_of(response: dict) -> str:
        try:
            blocks = response["output"]["message"]["content"]
        except (KeyError, TypeError) as error:
            raise PermanentModelError("unexpected response shape") from error
        text = "".join(block["text"] for block in blocks if "text" in block)
        if not text:
            raise PermanentModelError("the response has no text")
        return text

    @staticmethod
    def _log(started: float, **result: object) -> None:
        logger.info(
            "outgoing call to model",
            extra={
                "target": "bedrock",
                "duration_ms": round((time.monotonic() - started) * 1000),
                **result,
            },
        )
