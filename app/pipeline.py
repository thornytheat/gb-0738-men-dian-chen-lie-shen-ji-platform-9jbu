from functools import cached_property

from app.modules.confirmations.service import resolve
from app.modules.coverage.service import coverage
from app.modules.intake.service import acquire, receive
from app.modules.penalties.service import judge
from app.modules.photos.service import collect_evidence
from app.modules.plans.service import plan_shelf
from app.modules.settlement.reconcile import reconcile


class Audit:
    def __init__(self, settings, options, store=None):
        self.bundle = acquire(settings.sample_root, options.dataset)
        self.options = options
        self.store = store

    @cached_property
    def intake(self):
        return receive(self.bundle)

    @property
    def sample_size(self):
        return self.options.sample_size or self.intake['campaign']['sample_size']

    @cached_property
    def confirmations(self):
        return self.store.confirmations(dataset=self.options.dataset) if self.store is not None else {}

    @cached_property
    def plans(self):
        return plan_shelf(self.intake, self.confirmations)

    @cached_property
    def photos(self):
        return collect_evidence(self.intake, self.plans, self.bundle)

    @cached_property
    def coverage(self):
        return coverage(self.intake, self.plans, self.photos, self.sample_size)

    @cached_property
    def penalties(self):
        return judge(self.coverage, self.intake['campaign'])

    @cached_property
    def settlement(self):
        return reconcile(self.intake, self.plans, self.photos, self.coverage, self.penalties)
