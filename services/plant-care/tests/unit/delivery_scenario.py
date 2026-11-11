import uuid
from datetime import timedelta

from app.service.escalation_publisher import EscalationPublisher
from app.service.escalation_repeater import EscalationRepeater
from app.service.model import DiagnosisStatus, PhotoKind
from tests.unit.delivery_fakes import DeliveryUnitOfWork, FakeEscalationEvents
from tests.unit.escalation_fakes import FakeExpertQuota
from tests.unit.fakes import NOW

USER = uuid.uuid4()
TTL = timedelta(hours=24)
ACK_MARGIN = timedelta(minutes=15)
GRACE = timedelta(seconds=30)
HISTORY_LIMIT = 5
BATCH = 10


class DeliveryScenario:
    """A user with an escalated update that has photos and a completed diagnosis."""

    def __init__(self) -> None:
        self.uow = DeliveryUnitOfWork()
        self.events = FakeEscalationEvents()
        self.quota = FakeExpertQuota()
        self.journal = self.uow.journals.seed(USER, species="Mint")
        self.update = self.uow.updates.seed(self.journal.id, created_at=NOW)
        for kind in (PhotoKind.OVERVIEW, PhotoKind.SOIL):
            self.uow.photos.add(self.update.id, kind, f"journals/{kind.value}")
        self.uow.diagnoses.seed(
            self.update.id,
            status=DiagnosisStatus.COMPLETED,
            has_dominant_candidate=False,
            clarifying_question="Is the soil soggy?",
            clarifying_answer="Yes",
            recommendation="Water less.",
            escalation_offered=True,
        )
        self.publisher = EscalationPublisher(
            lambda: self.uow, self.events, TTL, ACK_MARGIN, HISTORY_LIMIT
        )
        self.repeater = EscalationRepeater(
            lambda: self.uow, self.publisher, self.quota, GRACE, TTL, BATCH
        )

    def escalate(self, **overrides):
        return self.uow.escalations.seed(self.update.id, **overrides)

    def at(self, age: timedelta, **overrides):
        """An escalation created `age` before the database clock."""
        return self.uow.escalations.seed(
            self.update.id, created_at=self.uow.escalation_delivery.clock - age, **overrides
        )

    def second_update(self):
        return self.uow.updates.seed(self.journal.id, created_at=NOW + timedelta(hours=1))
