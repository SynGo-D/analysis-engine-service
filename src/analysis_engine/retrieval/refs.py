from ..domain import Finding

# Fingerprints are 64-character SHA-256 hex strings. An agent sees dozens
# of findings per review and has to quote the ones it refers to, so a full
# fingerprint would cost roughly 4x the tokens of this prefix for no gain:
# 10 hex characters can't realistically collide within one analysis, and
# `resolve_finding_ref` refuses an ambiguous prefix anyway.
FINDING_REF_LENGTH = 10


def finding_ref(finding: Finding) -> str:
    return finding.fingerprint[:FINDING_REF_LENGTH]


def resolve_finding_ref(ref: str, findings: list[Finding]) -> Finding | None:
    """The finding an agent's short reference points to, or None if it matches zero or several."""
    ref = ref.strip().lower()
    if len(ref) < FINDING_REF_LENGTH:
        return None
    matches = [f for f in findings if f.fingerprint.startswith(ref)]
    return matches[0] if len(matches) == 1 else None
