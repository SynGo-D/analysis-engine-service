"""
Measuring complexity across every function, rather than only the ones
that broke a rule.

The review pass reports a function when it exceeds a threshold. Deriving
"average complexity" from those reports answers a different question than
the one the dashboard asks: it is the average *among functions that were
already too complex*, which is biased upward and — worse — missing
entirely for healthy code. A clean repository showed a dash, which reads
as a broken analyzer rather than as good news.

So the measurement runs the same tools a second time with their
thresholds at the floor, where every function reports its value. Nothing
here becomes a Finding: a function with a complexity of 1 is a data
point, not a problem.

The cost is one extra tool invocation per language. Measured against the
production timings, the analyzers account for roughly 5 seconds of a
50-second job, nearly all of which is the AI review — so this is noise.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from .process_runner_helpers import run_measurement

logger = logging.getLogger(__name__)

# Both tools name the measured value in the message; this is the only
# place that parsing happens for the measurement pass.
_ESLINT_CYCLOMATIC = re.compile(r"has a complexity of (\d+)")
_ESLINT_COGNITIVE = re.compile(r"Cognitive Complexity from (\d+) to")
_PMD_CYCLOMATIC = re.compile(r"has a cyclomatic complexity of (\d+)")
_PMD_COGNITIVE = re.compile(r"has a cognitive complexity of (\d+)")


@dataclass
class ComplexityMeasurements:
    """
    One entry per function, not per violation.

    `cognitive` is keyed by location because the tools report the two
    metrics separately: a function with a cognitive complexity of zero is
    not reported at all, and treating "absent" as "excluded" would inflate
    the average. Matching on (file, line) lets an unreported function
    count as the zero it is.
    """

    cyclomatic: dict[tuple[str, int], int] = field(default_factory=dict)
    cognitive: dict[tuple[str, int], int] = field(default_factory=dict)

    @property
    def functions(self) -> int:
        return len(self.cyclomatic)

    def cyclomatic_values(self) -> list[int]:
        return list(self.cyclomatic.values())

    def cognitive_values(self) -> list[int]:
        """
        Zero for every measured function the cognitive rule did not
        report — those functions exist and their complexity is zero.
        """
        return [self.cognitive.get(location, 0) for location in self.cyclomatic]

    def merge(self, other: "ComplexityMeasurements") -> None:
        self.cyclomatic.update(other.cyclomatic)
        self.cognitive.update(other.cognitive)


async def measure_complexity(workspace_path: Path, languages: frozenset[str]) -> ComplexityMeasurements:
    """
    Runs each applicable tool's measurement pass. A failure here is never
    fatal: the metrics lose their average and the review is unaffected,
    which is a far better outcome than failing a job over a statistic.
    """
    measurements = ComplexityMeasurements()

    if languages & {"javascript", "typescript"}:
        try:
            measurements.merge(await _measure_javascript(workspace_path))
        except Exception as error:
            logger.warning("complexity measurement failed for javascript/typescript: %s", error)

    if "java" in languages:
        try:
            measurements.merge(await _measure_java(workspace_path))
        except Exception as error:
            logger.warning("complexity measurement failed for java: %s", error)

    if measurements.functions:
        logger.info("measured complexity of %d function(s)", measurements.functions)

    return measurements


async def _measure_javascript(workspace_path: Path) -> ComplexityMeasurements:
    result = await run_measurement(
        [
            settings.eslint_bin_path,
            "--config", settings.eslint_measure_config_path,
            "--no-config-lookup",
            "--format", "json",
            ".",
        ],
        cwd=workspace_path,
        timeout=settings.analyzer_timeout_seconds,
        # ESLint exits 1 when it reports anything, which here is the
        # normal outcome — every function is "reported".
        success_codes=(0, 1),
    )

    measurements = ComplexityMeasurements()
    if not result.strip():
        return measurements

    for file_report in json.loads(result):
        path = _relative(file_report["filePath"], workspace_path)
        for message in file_report.get("messages", []):
            location = (path, message.get("line", 0))
            text = message.get("message", "")

            if match := _ESLINT_CYCLOMATIC.search(text):
                measurements.cyclomatic[location] = int(match.group(1))
            elif match := _ESLINT_COGNITIVE.search(text):
                measurements.cognitive[location] = int(match.group(1))

    return measurements


async def _measure_java(workspace_path: Path) -> ComplexityMeasurements:
    result = await run_measurement(
        [
            settings.pmd_bin_path,
            "check",
            "--dir", str(workspace_path),
            "--rulesets", settings.pmd_measure_ruleset_path,
            "--format", "json",
            "--no-progress",
            "--no-fail-on-error",
        ],
        cwd=workspace_path,
        timeout=settings.java_analyzer_timeout_seconds,
        # 4 means "violations found", which here means "functions measured".
        success_codes=(0, 4),
    )

    measurements = ComplexityMeasurements()
    if not result.strip():
        return measurements

    for file_report in json.loads(result).get("files", []):
        path = _relative(file_report["filename"], workspace_path)
        for violation in file_report.get("violations", []):
            location = (path, violation.get("beginline", 0))
            text = violation.get("description", "")

            # PMD reports class-level counts through the same rules; only
            # methods are functions.
            if "The class" in text:
                continue

            if match := _PMD_CYCLOMATIC.search(text):
                measurements.cyclomatic[location] = int(match.group(1))
            elif match := _PMD_COGNITIVE.search(text):
                measurements.cognitive[location] = int(match.group(1))

    return measurements


def _relative(absolute_path: str, workspace_path: Path) -> str:
    try:
        return Path(absolute_path).relative_to(workspace_path).as_posix()
    except ValueError:
        return absolute_path
