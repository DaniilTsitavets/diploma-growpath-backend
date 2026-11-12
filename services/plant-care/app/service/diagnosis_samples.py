import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor

from app.service.diagnosis_model import DiagnosisModel, DiagnosisRequest, ModelAnswer, ModelFailure

logger = logging.getLogger(__name__)


def collect_answers(
    model: DiagnosisModel, request: DiagnosisRequest, samples: int
) -> list[ModelAnswer]:
    """The answers of `samples` parallel calls, in call order; failed calls are dropped.

    Raises the first failure when no call succeeded.
    """
    if samples == 1:
        return [model.diagnose(request)]
    with ThreadPoolExecutor(max_workers=samples, thread_name_prefix="diagnosis-sample") as pool:
        # A copy per call keeps the correlation ID of the request in the worker threads.
        futures = [
            pool.submit(contextvars.copy_context().run, model.diagnose, request)
            for _ in range(samples)
        ]
    answers: list[ModelAnswer] = []
    failures: list[ModelFailure] = []
    for future in futures:
        try:
            answers.append(future.result())
        except ModelFailure as failure:
            failures.append(failure)
    if not answers:
        raise failures[0]
    if failures:
        logger.warning(
            "some model calls failed, the answers cannot be cross-checked",
            extra={"samples": samples, "failed": len(failures)},
        )
    return answers
