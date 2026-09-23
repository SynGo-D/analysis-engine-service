import re

REDACTED = "[REDACTED]"

# Credentials that end up committed to repositories often enough to
# matter. Everything an agent reads passes through `redact` first, because
# whatever reaches the prompt is sent to the model provider.
#
# Deliberately specific: each pattern matches a credential's known shape.
# A broad "long random string" rule would also blank out hashes, IDs and
# test fixtures, and a reviewer that can't see the code can't review it.
_TOKEN_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key ID
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),                # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),              # GitHub fine-grained PAT
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),                  # GitLab PAT
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),      # OpenAI / Anthropic keys
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),             # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                     # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
]

# `password = "hunter2hunter2"` and friends: only the quoted value is
# replaced, so the reviewer still sees that a credential is hard-coded —
# which is itself worth flagging.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key)[\w.-]*
     ["']?\s*[:=]\s*)
    (["'])([^"'\s]{8,})\2
    """
)

# Credentials embedded in URLs: https://user:token@host
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)")


def redact(text: str) -> str:
    """Replaces likely credentials with [REDACTED], keeping the surrounding code."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(2)}", text)
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)
