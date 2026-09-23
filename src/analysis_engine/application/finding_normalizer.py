import hashlib

from ..domain import Finding


class FindingNormalizer:
    """
    Finding Normalizer — sits between analyzer/Reviewdog output and the
    rest of the pipeline (see root README's architecture diagram).
    Computes each Finding's real fingerprint (all four analyzers leave it
    as "" — see analyzers/README.md) and deduplicates by it.

    Centralizing fingerprint computation here, rather than having each of
    the four analyzers compute its own, keeps that logic in one place —
    the same "Analysis Engine owns domain-level result processing"
    principle that already justified keeping business logic out of
    individual tool integrations.

    Category refinement (currently coarse, tool-output-type-level rather
    than per-rule — see each analyzer's _to_finding) is deliberately not
    attempted here. That's its own substantial task across four tools'
    full rule sets; in scope for this phase is specifically fingerprint
    and deduplication, per its name.
    """

    def normalize(self, findings: list[Finding]) -> list[Finding]:
        fingerprinted = [self._with_fingerprint(f) for f in findings]
        return self._deduplicate(fingerprinted)

    def _with_fingerprint(self, finding: Finding) -> Finding:
        return finding.model_copy(update={"fingerprint": self._compute_fingerprint(finding)})

    def _compute_fingerprint(self, finding: Finding) -> str:
        """
        Deliberately scoped to "the same commit re-analyzed shouldn't
        duplicate findings" — the literal requirement — not "the same
        logical issue tracked across different commits". The latter is a
        materially harder problem (stable identity despite code shifting
        line numbers, file renames, etc.) that would need excluding line
        number and hashing something like surrounding code content
        instead. Worth a future iteration, not attempted here.

        Included: repository, commit_sha, tool, rule_id, file_path, line,
        column — enough to treat two *separate* instances of the same
        rule violation in the same file (e.g. two distinct "unused
        variable" warnings on different lines) as genuinely different
        findings, not collapsed into one by mistake.

        Excluded: message — tool output wording can vary trivially
        between versions without the underlying issue actually differing,
        which would make the fingerprint unnecessarily brittle.
        """
        raw = "|".join([
            finding.repository,
            finding.commit_sha,
            finding.tool,
            finding.rule_id,
            finding.file_path,
            str(finding.line),
            str(finding.column),
        ])
        return hashlib.sha256(raw.encode()).hexdigest()

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """
        First-wins by fingerprint. Two findings sharing a fingerprint
        should already be functionally identical in content (the same
        tool deterministically produces the same output for the same
        rule + location + commit), so no merge logic beyond keeping one
        is needed.
        """
        seen: dict[str, Finding] = {}
        for finding in findings:
            seen.setdefault(finding.fingerprint, finding)
        return list(seen.values())
