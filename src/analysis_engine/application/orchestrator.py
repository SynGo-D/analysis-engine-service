from ..domain import AnalysisJob, AnalysisResult


class AnalysisOrchestrator:
    """
    Coordinates the full analysis pipeline for one job:

        validate job -> obtain repository -> create workspace
          -> checkout commit -> detect languages -> select analyzers
          -> execute via Reviewdog -> normalize findings
          -> calculate quality/technical-debt metrics -> persist results
          -> publish completion event

    This is the only place that sequence is encoded (Pipeline/Command
    Pattern) — the consumer just calls `run()` and turns the outcome into
    an ack/nack. Filled in incrementally across Phases 4-10; each stage's
    real implementation replaces a piece of this stub without the
    consumer changing at all.
    """

    async def run(self, job: AnalysisJob) -> AnalysisResult:
        raise NotImplementedError(
            "AnalysisOrchestrator.run is implemented incrementally across "
            "Phases 4-10 (workspace, analyzers, normalization, technical "
            "debt, persistence, completion events)."
        )
