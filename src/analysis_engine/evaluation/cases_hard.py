"""
Harder evaluation cases. The basic set proved the pipeline works; these
target the ways a reviewer fails on real pull requests:

- a bug buried among legitimate changes (multi-file PRs, big refactors);
- more than one bug in a PR (does it stop after the first?);
- a bug that shows only in code the PR didn't touch (callers of a
  changed contract);
- domain bugs that need reasoning, not pattern matching (idempotency,
  token verification, timezones, a threshold on the wrong total);
- larger clean PRs, where staying quiet is harder.

The refactor cases' `totalHours` first used `.reduce()`, which a review
correctly flagged: the old `for...of` accepted any iterable (a Set, say)
and `totalHours` is exported, so "no behaviour change" wasn't true. The
case was fixed to really preserve behaviour.
"""

from .case import Case, Expected

# ---------------------------------------------------------------------------
# A bug among legitimate changes (multi-file)
# ---------------------------------------------------------------------------

_MODELS = '''from dataclasses import dataclass, field


@dataclass
class Order:
    id: int
    customer_email: str
    amount_paid: float
    refunded: float = 0.0
    lines: list = field(default_factory=list)
'''

_EMAILS_BASE = '''from shop.utils import fmt_money


def receipt_text(order):
    return f"Thanks for your order #{order.id}. You paid {fmt_money(order.amount_paid)}."
'''

_UTILS_BASE = '''def fmt_money(value):
    return f"${value:,.2f}"
'''

_SERVICE_BASE = '''from shop.emails import receipt_text


def complete_order(order, mailer):
    mailer.send(order.customer_email, receipt_text(order))
'''

HARD_REFUND = Case(
    id="hard-refund-cap-among-noise",
    title="Partial refunds, plus money formatting cleanup",
    description=(
        "Adds refund(): partial refunds are allowed, but the total refunded can never exceed what the customer "
        "paid. Also renames fmt_money to format_money, adds refund emails, and logs completed orders."
    ),
    base={"shop/models.py": _MODELS, "shop/emails.py": _EMAILS_BASE, "shop/utils.py": _UTILS_BASE,
          "shop/service.py": _SERVICE_BASE},
    head={
        "shop/utils.py": '''def format_money(value):
    """Format an amount in dollars, e.g. 1234.5 -> "$1,234.50"."""
    return f"${value:,.2f}"
''',
        "shop/emails.py": '''from shop.utils import format_money


def receipt_text(order):
    return f"Thanks for your order #{order.id}. You paid {format_money(order.amount_paid)}."


def refund_text(order, amount):
    return f"We refunded {format_money(amount)} for order #{order.id}."
''',
        "shop/refunds.py": '''from shop.emails import refund_text


class RefundError(Exception):
    pass


def refund(order, amount, gateway, mailer):
    """Refund part or all of an order. Never refunds more than was paid in total."""
    if amount <= 0:
        raise RefundError("amount must be positive")
    if amount > order.amount_paid:
        raise RefundError("cannot refund more than was paid")
    gateway.refund(order.id, amount)
    order.refunded += amount
    mailer.send(order.customer_email, refund_text(order, amount))
    return order.refunded
''',
        "shop/service.py": '''import logging

from shop.emails import receipt_text

logger = logging.getLogger(__name__)


def complete_order(order, mailer):
    mailer.send(order.customer_email, receipt_text(order))
    logger.info("order %s completed", order.id)
''',
    },
    expected=(Expected("shop/refunds.py", "if amount > order.amount_paid:",
                       "ignores earlier refunds: repeated partial refunds can exceed the amount paid"),),
)

# ---------------------------------------------------------------------------
# Two bugs in one PR
# ---------------------------------------------------------------------------

HARD_CACHE = Case(
    id="hard-cache-two-bugs",
    title="Cache permission lookups",
    description="Adds a small TTL cache (TTL in seconds) and uses it to cache each user's permissions for 60 seconds.",
    base={"src/permissions.js": '''async function loadPermissions(db, userId) {
  return db.query("SELECT permission FROM grants WHERE user_id = ?", [userId]);
}

module.exports = { loadPermissions };
'''},
    head={
        "src/ttlCache.js": '''/**
 * Small in-memory cache. `ttlSeconds` is how long an entry stays valid.
 */
class TtlCache {
  constructor(ttlSeconds) {
    this.ttlSeconds = ttlSeconds;
    this.entries = new Map();
  }

  key(userId, resource) {
    return userId + resource;
  }

  get(userId, resource) {
    const entry = this.entries.get(this.key(userId, resource));
    if (!entry || entry.expiresAt < Date.now()) {
      return undefined;
    }
    return entry.value;
  }

  set(userId, resource, value) {
    this.entries.set(this.key(userId, resource), {
      value,
      expiresAt: Date.now() + this.ttlSeconds,
    });
  }
}

module.exports = { TtlCache };
''',
        "src/permissions.js": '''const { TtlCache } = require("./ttlCache");

const cache = new TtlCache(60);

async function loadPermissions(db, userId) {
  const cached = cache.get(userId, "permissions");
  if (cached) {
    return cached;
  }
  const permissions = await db.query("SELECT permission FROM grants WHERE user_id = ?", [userId]);
  cache.set(userId, "permissions", permissions);
  return permissions;
}

module.exports = { loadPermissions };
''',
    },
    expected=(
        Expected("src/ttlCache.js", "expiresAt: Date.now() + this.ttlSeconds,",
                 "TTL in seconds added to a millisecond clock: entries expire after 60 ms, not 60 s"),
        Expected("src/ttlCache.js", "return userId + resource;",
                 "keys concatenated without a separator can collide across users (1+'23' vs 12+'3')"),
    ),
)

# ---------------------------------------------------------------------------
# The bug is in code the PR didn't touch
# ---------------------------------------------------------------------------

HARD_CONTRACT = Case(
    id="hard-changed-contract-breaks-callers",
    title="Friendly message for unknown accounts",
    description=(
        "get_account() now returns None for unknown ids instead of raising AccountNotFound, so the admin page "
        "can show a friendly message."
    ),
    base={
        "accounts/repository.py": '''class AccountNotFound(Exception):
    pass


ACCOUNTS = {}


def get_account(account_id):
    try:
        return ACCOUNTS[account_id]
    except KeyError:
        raise AccountNotFound(account_id) from None
''',
        "accounts/transfers.py": '''from accounts.repository import get_account


def transfer(source_id, target_id, amount):
    source = get_account(source_id)
    target = get_account(target_id)
    if source.balance < amount:
        raise ValueError("insufficient funds")
    source.balance -= amount
    target.balance += amount
''',
        "accounts/admin.py": '''from accounts.repository import AccountNotFound, get_account


def account_page(account_id):
    try:
        account = get_account(account_id)
    except AccountNotFound:
        return "error"
    return f"{account.owner}: {account.balance}"
''',
    },
    head={
        "accounts/repository.py": '''class AccountNotFound(Exception):
    pass


ACCOUNTS = {}


def get_account(account_id):
    """The account with this id, or None if there isn't one."""
    return ACCOUNTS.get(account_id)
''',
        "accounts/admin.py": '''from accounts.repository import get_account


def account_page(account_id):
    account = get_account(account_id)
    if account is None:
        return "No account with that id."
    return f"{account.owner}: {account.balance}"
''',
    },
    expected=(Expected("accounts/transfers.py", "source = get_account(source_id)",
                       "transfer() still expects an exception: unknown ids now fail later with AttributeError",
                       also=(("accounts/repository.py", "return ACCOUNTS.get(account_id)"),)),),
)

# ---------------------------------------------------------------------------
# Domain reasoning
# ---------------------------------------------------------------------------

HARD_IDEMPOTENCY = Case(
    id="hard-retry-double-charge",
    title="Retry transient payment failures",
    description="charge_with_retry() retries transient gateway errors up to 3 times. A customer must never be charged twice.",
    base={"payments/gateway.py": '''class TransientError(Exception):
    pass


class PaymentFailed(Exception):
    pass
'''},
    head={"payments/charging.py": '''import uuid

from payments.gateway import PaymentFailed, TransientError


def charge_with_retry(gateway, order, attempts=3):
    """Charge an order, retrying transient failures. Never charges twice."""
    for _ in range(attempts):
        try:
            return gateway.charge(order.id, order.total, idempotency_key=str(uuid.uuid4()))
        except TransientError:
            continue
    raise PaymentFailed(order.id)
'''},
    expected=(Expected("payments/charging.py", "idempotency_key=str(uuid.uuid4())",
                       "a new idempotency key per attempt: a retry after a timed-out success charges twice"),),
)

HARD_JWT = Case(
    id="hard-jwt-decode-not-verify",
    title="Simplify token parsing in auth middleware",
    description="Shorter userFromRequest(). Same behaviour: returns the user from a valid bearer token, otherwise null.",
    base={"src/auth.js": '''const jwt = require("jsonwebtoken");

const SECRET = process.env.JWT_SECRET;

function userFromRequest(req) {
  const token = (req.headers.authorization || "").replace("Bearer ", "");
  try {
    const payload = jwt.verify(token, SECRET);
    return { id: payload.sub, role: payload.role };
  } catch (error) {
    return null;
  }
}

module.exports = { userFromRequest };
'''},
    head={"src/auth.js": '''const jwt = require("jsonwebtoken");

function userFromRequest(req) {
  const token = (req.headers.authorization || "").replace("Bearer ", "");
  const payload = jwt.decode(token);
  return payload ? { id: payload.sub, role: payload.role } : null;
}

module.exports = { userFromRequest };
'''},
    expected=(Expected("src/auth.js", "const payload = jwt.decode(token);",
                       "decode() doesn't check the signature: anyone can forge a token with any role"),),
)

HARD_TIMEZONE = Case(
    id="hard-naive-vs-aware-datetime",
    title="Expire lapsed subscriptions",
    description="Adds expire_due(), run nightly, which marks subscriptions past their end date as expired.",
    base={"billing/subscriptions.py": '''from datetime import datetime, timedelta, timezone


def start_subscription(sub, days=30):
    sub.status = "active"
    sub.expires_at = datetime.now(timezone.utc) + timedelta(days=days)
'''},
    head={"billing/subscriptions.py": '''from datetime import datetime, timedelta, timezone


def start_subscription(sub, days=30):
    sub.status = "active"
    sub.expires_at = datetime.now(timezone.utc) + timedelta(days=days)


def expire_due(subscriptions):
    """Mark subscriptions whose end date has passed as expired."""
    now = datetime.now()
    for sub in subscriptions:
        if sub.expires_at < now:
            sub.status = "expired"
'''},
    expected=(Expected("billing/subscriptions.py", "now = datetime.now()",
                       "naive now() compared with UTC-aware expires_at: raises TypeError on the first subscription"),),
)

HARD_THRESHOLD = Case(
    id="hard-threshold-wrong-total",
    title="Free shipping over $50",
    description="Orders of $50 or more ship free. The threshold is measured before tax, as in our pricing policy.",
    base={"shop/order.py": '''from dataclasses import dataclass


@dataclass
class Order:
    subtotal: float
    tax_rate: float = 0.2

    @property
    def total_with_tax(self):
        return round(self.subtotal * (1 + self.tax_rate), 2)
'''},
    head={"shop/shipping.py": '''FREE_SHIPPING_THRESHOLD = 50
STANDARD_SHIPPING = 4.99


def shipping_cost(order):
    if order.total_with_tax >= FREE_SHIPPING_THRESHOLD:
        return 0
    return STANDARD_SHIPPING
'''},
    expected=(Expected("shop/shipping.py", "if order.total_with_tax >= FREE_SHIPPING_THRESHOLD:",
                       "threshold checked against the taxed total; policy says before tax (a $42 order ships free)"),),
)

# ---------------------------------------------------------------------------
# A behaviour change buried in a large "no behaviour change" refactor
# ---------------------------------------------------------------------------

_REPORTS_BASE = '''function totalHours(entries) {
  let total = 0;
  for (const entry of entries) {
    total += entry.hours;
  }
  return total;
}

function listProjects(projects, { includeArchived = false } = {}) {
  const visible = projects.filter((p) => includeArchived || !p.archived);
  return visible.sort((a, b) => a.name.localeCompare(b.name));
}

function summarizeProjects(projects, entries) {
  const rows = [];
  for (const project of listProjects(projects)) {
    const projectEntries = entries.filter((e) => e.projectId === project.id);
    rows.push({ name: project.name, hours: totalHours(projectEntries) });
  }
  return rows;
}

function csvExport(projects, entries) {
  const rows = summarizeProjects(projects, entries);
  const lines = ["project,hours"];
  for (const row of rows) {
    lines.push(row.name + "," + row.hours.toFixed(1));
  }
  return lines.join("\\n");
}

module.exports = { totalHours, listProjects, summarizeProjects, csvExport };
'''

_REPORTS_HEAD = '''/** Sum of hours across time entries (any iterable, as before). */
function totalHours(entries) {
  let total = 0;
  for (const entry of entries) {
    total += entry.hours;
  }
  return total;
}

/** Projects sorted by name, without archived ones unless asked for. */
function listProjects(projects, { includeArchived = true } = {}) {
  return projects
    .filter((project) => includeArchived || !project.archived)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function entriesFor(project, entries) {
  return entries.filter((entry) => entry.projectId === project.id);
}

function summaryRow(project, entries) {
  return { name: project.name, hours: totalHours(entriesFor(project, entries)) };
}

/** One row per visible project with its total hours. */
function summarizeProjects(projects, entries) {
  return listProjects(projects).map((project) => summaryRow(project, entries));
}

function csvLine(row) {
  return `${row.name},${row.hours.toFixed(1)}`;
}

/** Summary as CSV with a header row. */
function csvExport(projects, entries) {
  const rows = summarizeProjects(projects, entries);
  return ["project,hours", ...rows.map(csvLine)].join("\\n");
}

module.exports = { totalHours, listProjects, summarizeProjects, csvExport };
'''

HARD_REFACTOR = Case(
    id="hard-refactor-hidden-default-flip",
    title="Refactor reports into smaller functions",
    description="Splits the reports module into small helpers and adds doc comments. No behaviour change.",
    base={"src/reports.js": _REPORTS_BASE},
    head={"src/reports.js": _REPORTS_HEAD},
    expected=(Expected("src/reports.js", "function listProjects(projects, { includeArchived = true } = {}) {",
                       "default flipped to true: archived projects now appear in every summary and export"),),
)

# ---------------------------------------------------------------------------
# Larger clean PRs
# ---------------------------------------------------------------------------

HARD_CLEAN_REFACTOR = Case(
    id="hard-clean-large-refactor",
    title="Refactor reports into smaller functions",
    description="Splits the reports module into small helpers and adds doc comments. No behaviour change.",
    base={"src/reports.js": _REPORTS_BASE},
    head={"src/reports.js": _REPORTS_HEAD.replace("{ includeArchived = true }", "{ includeArchived = false }")},
)

HARD_CLEAN_FEATURE = Case(
    id="hard-clean-order-notes",
    title="Let customers add a note to an order",
    description="Adds an optional note (max 500 characters) shown on the receipt. Also renames fmt_money to format_money.",
    base={"shop/models.py": _MODELS, "shop/emails.py": _EMAILS_BASE, "shop/utils.py": _UTILS_BASE},
    head={
        "shop/models.py": '''from dataclasses import dataclass, field

MAX_NOTE_LENGTH = 500


@dataclass
class Order:
    id: int
    customer_email: str
    amount_paid: float
    refunded: float = 0.0
    lines: list = field(default_factory=list)
    note: str = ""

    def set_note(self, note):
        """Attach the customer's note. Raises ValueError if it's too long."""
        note = note.strip()
        if len(note) > MAX_NOTE_LENGTH:
            raise ValueError(f"note is longer than {MAX_NOTE_LENGTH} characters")
        self.note = note
''',
        "shop/utils.py": '''def format_money(value):
    """Format an amount in dollars, e.g. 1234.5 -> "$1,234.50"."""
    return f"${value:,.2f}"
''',
        "shop/emails.py": '''from shop.utils import format_money


def receipt_text(order):
    text = f"Thanks for your order #{order.id}. You paid {format_money(order.amount_paid)}."
    if order.note:
        text += f"\\nYour note: {order.note}"
    return text
''',
        "tests/test_notes.py": '''import pytest

from shop.models import MAX_NOTE_LENGTH, Order


def test_note_is_trimmed_and_stored():
    order = Order(id=1, customer_email="a@b.c", amount_paid=10)
    order.set_note("  leave at the door  ")
    assert order.note == "leave at the door"


def test_overlong_note_is_rejected():
    order = Order(id=1, customer_email="a@b.c", amount_paid=10)
    with pytest.raises(ValueError):
        order.set_note("x" * (MAX_NOTE_LENGTH + 1))
''',
    },
)

HARD_CASES: tuple[Case, ...] = (
    HARD_REFUND, HARD_CACHE, HARD_CONTRACT, HARD_IDEMPOTENCY, HARD_JWT, HARD_TIMEZONE, HARD_THRESHOLD,
    HARD_REFACTOR, HARD_CLEAN_REFACTOR, HARD_CLEAN_FEATURE,
)
