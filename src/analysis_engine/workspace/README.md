# workspace

Isolated temporary workspace management — one workspace per analysis job.
Responsible for secure clone/checkout, path sanitization (repository
source is untrusted input), execution timeouts and resource limits, and
guaranteed cleanup after each job (success or failure).

Planned: Phase 4.
