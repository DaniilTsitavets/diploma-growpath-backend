import uuid

from app.service.diagnosis import DiagnosisService
from app.service.effort import EffortPolicy
from app.service.errors import AiQuotaUnavailable
from app.service.model import Diagnosis, PhotoKind, Tier
from tests.unit.fake_model import FakeModel
from tests.unit.fakes import FakeAiQuota, FakeUnitOfWork

USER = uuid.uuid4()


class StaticTiers:
    def __init__(self, tier: Tier | None) -> None:
        self.tier = tier

    def tier_of(self, user_id: uuid.UUID) -> Tier:
        if self.tier is None:
            raise AiQuotaUnavailable
        return self.tier


class Scenario:
    """A user with an update that has the required photos and a pending diagnosis."""

    def __init__(self, tier: Tier | None = Tier.HOBBYIST) -> None:
        self.uow = FakeUnitOfWork()
        self.quota = FakeAiQuota()
        self.tiers = StaticTiers(tier)
        journal = self.uow.journals.seed(USER)
        self.update_id = self.uow.updates.seed(journal.id).id
        for kind in (PhotoKind.OVERVIEW, PhotoKind.SOIL):
            self.uow.photos.add(self.update_id, kind, f"key/{kind.value}")
        self.reservation_id = uuid.uuid4()
        self.quota.reserved.append(self.reservation_id)
        self.uow.diagnoses.seed(self.update_id, self.reservation_id)

    def service(self, model: FakeModel) -> DiagnosisService:
        return DiagnosisService(lambda: self.uow, self.quota, model, EffortPolicy(self.tiers))

    @property
    def row(self) -> Diagnosis:
        return self.uow.diagnoses.states[self.update_id]

    def seed(self, **summary) -> None:
        self.uow.diagnoses.seed(self.update_id, self.row.ai_reservation_id, **summary)
