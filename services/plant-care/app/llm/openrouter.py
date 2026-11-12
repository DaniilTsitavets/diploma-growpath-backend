import base64
import logging
import time

import httpx
from pydantic import SecretStr

from app.llm.transport import ModelPrompt, PermanentModelError, TransientModelError
from app.service.model import ModelEffort

logger = logging.getLogger(__name__)

TRANSIENT_STATUSES = frozenset({408, 429})


class OpenRouterTransport:
    """Chat completions of OpenRouter with vision input.

    Photos travel as base64 data URLs: a presigned URL of the local object store is not reachable
    from the provider.
    """

    def __init__(
        self,
        http: httpx.Client,
        url: str,
        api_key: SecretStr,
        model: str,
        max_output_tokens: int,
    ) -> None:
        self._http = http
        self._url = url
        self._api_key = api_key
        self._model = model
        self._max_output_tokens = max_output_tokens

    def complete(
        self, prompt: ModelPrompt, effort: ModelEffort, temperature: float | None = None
    ) -> str:
        started = time.monotonic()
        try:
            response = self._http.post(
                self._url, json=self._payload(prompt, effort, temperature), headers=self._headers()
            )
        except httpx.HTTPError as error:
            self._log(started, outcome=type(error).__name__)
            raise TransientModelError from error
        self._log(started, status=response.status_code)
        if response.status_code >= 500 or response.status_code in TRANSIENT_STATUSES:
            raise TransientModelError(response.status_code)
        if response.status_code != 200:
            raise PermanentModelError(response.status_code)
        return self._text_of(response)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    def _payload(self, prompt: ModelPrompt, effort: ModelEffort, temperature: float | None) -> dict:
        images = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image.media_type};base64,{base64.b64encode(image.data).decode()}"
                },
            }
            for image in prompt.images
        ]
        payload = {
            "model": self._model,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
            "reasoning": {"effort": effort.value},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": [{"type": "text", "text": prompt.user}, *images]},
            ],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    @staticmethod
    def _text_of(response: httpx.Response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise PermanentModelError("unexpected response shape") from error
        if not isinstance(content, str):
            raise PermanentModelError("unexpected response shape")
        return content

    @staticmethod
    def _log(started: float, **result: object) -> None:
        logger.info(
            "outgoing call to model",
            extra={
                "target": "openrouter",
                "duration_ms": round((time.monotonic() - started) * 1000),
                **result,
            },
        )
