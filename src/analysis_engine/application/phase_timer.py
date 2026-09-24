"""
Stage timings for one analysis job.

The AI review already reports its own rounds, tokens, cost and duration
(domain/agent_review.py). Everything before it — waiting in the queue,
cloning, detecting languages, running the analyzers, working out the
diff, indexing, writing to the database — reported nothing, so "the
review takes 34 seconds" could not be broken down into where the time
actually went.

That matters for deciding what to optimise. Caching the repository scan
is worth doing if scanning dominates and is waste if it does not, and
the only way to tell them apart is to measure both.

Monotonic clock, not wall clock: the numbers are durations, and a clock
adjustment mid-job would otherwise produce a negative one.
"""
from contextlib import contextmanager
from time import monotonic


class PhaseTimer:
    """
    Records how long each named stage took, in milliseconds.

    Deliberately forgiving: a stage that raises still records its
    duration, because how long something took before it failed is
    usually the interesting part.
    """

    def __init__(self) -> None:
        self._phases: dict[str, int] = {}

    @contextmanager
    def phase(self, name: str):
        started = monotonic()
        try:
            yield
        finally:
            self.record(name, int((monotonic() - started) * 1000))

    def record(self, name: str, milliseconds: int) -> None:
        """
        Adds a duration measured elsewhere — the queue wait, or a stage
        that reports its own timing.

        Repeated names accumulate rather than overwrite, so a stage that
        runs once per analyzer sums to the total spent in it.
        """
        self._phases[name] = self._phases.get(name, 0) + max(0, milliseconds)

    def as_dict(self) -> dict[str, int]:
        return dict(self._phases)
