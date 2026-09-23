"""
The evaluation set (docs/agent-architecture.md §12).

Every planted defect is one the linters *can't* see — logic, intent and
security bugs — because that's the Reviewer's job. Linter-detectable
problems belong in linter triage, not here. Each case's title and
description state what the author meant to do, as a real PR would: some
bugs are only bugs relative to that intent.

Clean cases have no `expected` defects. Two are deliberate decoys: correct
code that looks suspicious at a glance.

Clean cases must be *genuinely* clean. The first real run (gpt-5.6-luna,
2026-09-21) flagged two of them, and both reports were correct:
math.isclose kept its default rel_tol (accepting a 1.2-cent mismatch on a
$20M invoice), and logging the raw username allowed forged log lines via
a newline. Both cases were fixed rather than counting the Reviewer's
correct findings as false alarms.
"""

from .case import Case, Expected

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

_PAGINATION_BASE = '''def paginate(items, page, page_size=20):
    """Return one page of items. Pages are numbered from 1."""
    start = (page - 1) * page_size
    return items[start:start + page_size]
'''

_ORDERS_API = '''from app.pagination import paginate


def list_orders(orders, page):
    return {"page": page, "orders": paginate(orders, page)}
'''

PY_PAGINATION = Case(
    id="py-pagination-off-by-one",
    title="Validate page numbers in pagination",
    description="Rejects page numbers below 1 with a ValueError. No behaviour change for valid pages.",
    base={"app/pagination.py": _PAGINATION_BASE, "app/orders_api.py": _ORDERS_API},
    head={"app/pagination.py": '''def paginate(items, page, page_size=20):
    """Return one page of items. Pages are numbered from 1."""
    if page < 1:
        raise ValueError("page must be 1 or more")
    start = page * page_size
    return items[start:start + page_size]
'''},
    expected=(Expected("app/pagination.py", "start = page * page_size",
                       "page 1 now skips the first page_size items (1-based page used as 0-based)"),),
)

PY_DISCOUNT = Case(
    id="py-discount-after-tax",
    title="Never let discounts make a total negative",
    description="Clamps the order total at zero. Discounts must still be applied before tax, as before.",
    base={"billing/total.py": '''def total(price, discount, tax_rate=0.2):
    """Discounts are applied before tax."""
    return (price - discount) * (1 + tax_rate)
'''},
    head={"billing/total.py": '''def total(price, discount, tax_rate=0.2):
    """Discounts are applied before tax. The total is never negative."""
    taxed = price * (1 + tax_rate)
    return max(taxed - discount, 0)
'''},
    expected=(Expected("billing/total.py", "return max(taxed - discount, 0)",
                       "discount is now subtracted after tax, contradicting the stated rule"),),
)

PY_PERMISSION = Case(
    id="py-inverted-permission",
    title="Archived projects can't be deleted",
    description="Adds a check so archived projects are never deleted. Admins and owners keep their existing rights otherwise.",
    base={
        "projects/permissions.py": '''def can_delete_project(user, project):
    return user.is_admin or project.owner_id == user.id
''',
        "projects/views.py": '''from projects.permissions import can_delete_project


def delete_project(user, project, repository):
    if not can_delete_project(user, project):
        raise PermissionError("not allowed")
    repository.delete(project.id)
''',
    },
    head={"projects/permissions.py": '''def can_delete_project(user, project):
    if project.archived:
        return False
    return not user.is_admin or project.owner_id == user.id
'''},
    expected=(Expected("projects/permissions.py", "return not user.is_admin or project.owner_id == user.id",
                       "admin check inverted: every non-admin may now delete any project"),),
)

PY_PATH_TRAVERSAL = Case(
    id="py-path-traversal",
    title="Let users download their uploads",
    description="Adds read_upload() and a download route for files users uploaded earlier.",
    base={"files/storage.py": '''import os

UPLOAD_DIR = "/srv/uploads"


def save_upload(filename, data):
    """Store an uploaded file. Only the base name is kept."""
    path = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    with open(path, "wb") as handle:
        handle.write(data)
'''},
    head={
        "files/storage.py": '''import os

UPLOAD_DIR = "/srv/uploads"


def save_upload(filename, data):
    """Store an uploaded file. Only the base name is kept."""
    path = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    with open(path, "wb") as handle:
        handle.write(data)


def read_upload(filename):
    """Return the contents of a file the user uploaded earlier."""
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "rb") as handle:
        return handle.read()
''',
        "files/routes.py": '''from files.storage import read_upload


def download(request):
    return read_upload(request.args["name"])
''',
    },
    expected=(Expected("files/storage.py", "path = os.path.join(UPLOAD_DIR, filename)",
                       "user-supplied name joined unsanitised: ../ reads any file on the server"),),
)

PY_RETRY = Case(
    id="py-swallowed-retry",
    title="Retry flaky fetches",
    description="Adds fetch_with_retry(): retries network errors with backoff and raises if every attempt fails.",
    base={"net/client.py": '''class NetworkError(Exception):
    pass


def fetch(url):
    raise NetworkError(url)
'''},
    head={"net/client.py": '''import time


class NetworkError(Exception):
    pass


def fetch(url):
    raise NetworkError(url)


def fetch_with_retry(url, attempts=3):
    """Fetch a URL, retrying on network errors. Raises if every attempt fails."""
    for attempt in range(attempts):
        try:
            return fetch(url)
        except NetworkError:
            time.sleep(2 ** attempt)
    return None
'''},
    expected=(Expected("net/client.py", "    return None",
                       "returns None after the last failure instead of raising, as documented"),),
)

PY_SWAPPED_ARGS = Case(
    id="py-swapped-arguments",
    title="Put currency first in convert()",
    description="Reorders convert()'s parameters to (currency, amount) to match the rest of the money API.",
    base={
        "money/convert.py": '''RATES = {"EUR": 0.9, "GBP": 0.8}


def convert(amount, currency):
    return round(amount * RATES[currency], 2)
''',
        "money/checkout.py": '''from money.convert import convert


def price_in(order, currency):
    return convert(order.total, currency)
''',
    },
    head={"money/convert.py": '''RATES = {"EUR": 0.9, "GBP": 0.8}


def convert(currency, amount):
    return round(amount * RATES[currency], 2)
'''},
    expected=(Expected("money/checkout.py", "return convert(order.total, currency)",
                       "existing caller not updated: passes (amount, currency) to (currency, amount)",
                       also=(("money/convert.py", "def convert(currency, amount):"),)),),
)

PY_CENTS = Case(
    id="py-float-cents-truncation",
    title="Simplify cents conversion",
    description="Tidies to_cents(). Pure refactor; results should be identical.",
    base={"payments/amounts.py": '''def to_cents(price):
    """Convert a decimal price like 19.99 to integer cents."""
    return round(price * 100)
'''},
    head={"payments/amounts.py": '''def to_cents(price):
    """Convert a decimal price like 19.99 to integer cents."""
    return int(price * 100)
'''},
    expected=(Expected("payments/amounts.py", "return int(price * 100)",
                       "int() truncates float error: 0.29 * 100 = 28.999... becomes 28 cents"),),
)

# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

JS_SORT = Case(
    id="js-numeric-sort",
    title="Simplify leaderboard sorting",
    description="Shortens topScores(). Should still return the n highest scores, highest first.",
    base={"src/leaderboard.js": '''function topScores(scores, n) {
  return [...scores].sort((a, b) => b - a).slice(0, n);
}

module.exports = { topScores };
'''},
    head={"src/leaderboard.js": '''function topScores(scores, n) {
  return [...scores].sort().reverse().slice(0, n);
}

module.exports = { topScores };
'''},
    expected=(Expected("src/leaderboard.js", "[...scores].sort().reverse()",
                       "default sort is lexicographic: 9 ranks above 10"),),
)

_USERS_JS = '''async function getUser(id) {
  return { id, active: true, name: "Ada" };
}

module.exports = { getUser };
'''

JS_AWAIT = Case(
    id="js-missing-await",
    title="Add profile export endpoint",
    description="Adds exportProfile(), which returns the same user data as showProfile() in export format.",
    base={
        "src/users.js": _USERS_JS,
        "src/profile.js": '''const { getUser } = require("./users");

async function showProfile(req, res) {
  const user = await getUser(req.params.id);
  if (!user.active) {
    return res.status(403).send("inactive");
  }
  return res.json(user);
}

module.exports = { showProfile };
''',
    },
    head={"src/profile.js": '''const { getUser } = require("./users");

async function showProfile(req, res) {
  const user = await getUser(req.params.id);
  if (!user.active) {
    return res.status(403).send("inactive");
  }
  return res.json(user);
}

async function exportProfile(req, res) {
  const user = getUser(req.params.id);
  if (!user.active) {
    return res.status(403).send("inactive");
  }
  return res.json({ exportedAt: Date.now(), user });
}

module.exports = { showProfile, exportProfile };
'''},
    expected=(Expected("src/profile.js", "const user = getUser(req.params.id);",
                       "missing await: user is a Promise, user.active is undefined, every export returns 403"),),
)

JS_XSS = Case(
    id="js-comment-xss",
    title="Keep line breaks in comments",
    description="Comments now render their line breaks.",
    base={"src/comments.js": '''function renderComment(container, comment) {
  const el = document.createElement("p");
  el.textContent = comment.text;
  container.appendChild(el);
}

module.exports = { renderComment };
'''},
    head={"src/comments.js": '''function renderComment(container, comment) {
  const el = document.createElement("p");
  el.innerHTML = comment.text.replace(/\\n/g, "<br>");
  container.appendChild(el);
}

module.exports = { renderComment };
'''},
    expected=(Expected("src/comments.js", 'el.innerHTML = comment.text.replace(/\\n/g, "<br>");',
                       "user-written comment inserted as HTML: stored XSS"),),
)

JS_MONTH = Case(
    id="js-month-off-by-one",
    title="Show the holiday banner in December",
    description="Adds isDecember() and uses it to show the seasonal banner.",
    base={"src/banner.js": '''function bannerText() {
  return "Welcome!";
}

module.exports = { bannerText };
'''},
    head={"src/banner.js": '''function isDecember(date) {
  return date.getMonth() === 12;
}

function bannerText(now = new Date()) {
  return isDecember(now) ? "Happy holidays!" : "Welcome!";
}

module.exports = { bannerText, isDecember };
'''},
    expected=(Expected("src/banner.js", "return date.getMonth() === 12;",
                       "getMonth() is 0-based (December is 11): the banner never shows"),),
)

JS_ROLE = Case(
    id="js-role-substring",
    title="Treat super-admins as admins",
    description="isAdmin() now also accepts the super-admin role.",
    base={
        "src/roles.js": '''// Every role the system assigns.
const ROLES = ["admin", "super-admin", "reader", "admin-requested"];

function isAdmin(user) {
  return user.role === "admin";
}

module.exports = { ROLES, isAdmin };
''',
    },
    head={"src/roles.js": '''// Every role the system assigns.
const ROLES = ["admin", "super-admin", "reader", "admin-requested"];

function isAdmin(user) {
  return user.role.includes("admin");
}

module.exports = { ROLES, isAdmin };
'''},
    expected=(Expected("src/roles.js", 'return user.role.includes("admin");',
                       'substring match: "admin-requested" (a pending request) now gets admin rights'),),
)

# ---------------------------------------------------------------------------
# Clean PRs — anything reported here is a false alarm
# ---------------------------------------------------------------------------

CLEAN_RENAME = Case(
    id="clean-rename",
    title="Rename calc_total to order_total",
    description="Pure rename, including its one caller.",
    base={
        "shop/orders.py": '''def calc_total(lines):
    return sum(line.price * line.quantity for line in lines)
''',
        "shop/receipt.py": '''from shop.orders import calc_total


def receipt(order):
    return f"Total: {calc_total(order.lines):.2f}"
''',
    },
    head={
        "shop/orders.py": '''def order_total(lines):
    return sum(line.price * line.quantity for line in lines)
''',
        "shop/receipt.py": '''from shop.orders import order_total


def receipt(order):
    return f"Total: {order_total(order.lines):.2f}"
''',
    },
)

CLEAN_VALIDATION = Case(
    id="clean-quantity-validation",
    title="Reject non-positive quantities",
    description="add_line() now raises ValueError for zero or negative quantities.",
    base={"shop/cart.py": '''class Cart:
    def __init__(self):
        self.lines = []

    def add_line(self, product, quantity):
        self.lines.append((product, quantity))
'''},
    head={"shop/cart.py": '''class Cart:
    def __init__(self):
        self.lines = []

    def add_line(self, product, quantity):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.lines.append((product, quantity))
'''},
)

CLEAN_FLOAT_DECOY = Case(
    id="clean-float-compare-decoy",
    title="Compare invoice totals with a tolerance",
    description="Replaces exact float equality with math.isclose to one cent.",
    base={"billing/reconcile.py": '''def totals_match(invoice_total, paid_total):
    return invoice_total == paid_total
'''},
    head={"billing/reconcile.py": '''import math


def totals_match(invoice_total, paid_total):
    return math.isclose(invoice_total, paid_total, rel_tol=0, abs_tol=0.005)
'''},
)

CLEAN_JS_REFACTOR = Case(
    id="clean-js-extract-helper",
    title="Extract formatPrice helper",
    description="Moves price formatting into a helper. Output is unchanged.",
    base={"src/prices.js": '''function describe(item) {
  return item.name + ": $" + item.price.toFixed(2);
}

module.exports = { describe };
'''},
    head={"src/prices.js": '''function formatPrice(price) {
  return "$" + price.toFixed(2);
}

function describe(item) {
  return item.name + ": " + formatPrice(item.price);
}

module.exports = { describe, formatPrice };
'''},
)

CLEAN_PAIRS_DECOY = Case(
    id="clean-adjacent-pairs-decoy",
    title="Detect unsorted timestamps",
    description="Adds isChronological(), which checks each timestamp against the next one.",
    base={"src/events.js": '''function latest(events) {
  return events[events.length - 1];
}

module.exports = { latest };
'''},
    head={"src/events.js": '''function latest(events) {
  return events[events.length - 1];
}

function isChronological(timestamps) {
  for (let i = 0; i < timestamps.length - 1; i++) {
    if (timestamps[i] > timestamps[i + 1]) {
      return false;
    }
  }
  return true;
}

module.exports = { latest, isChronological };
'''},
)

CLEAN_LOGGING = Case(
    id="clean-add-logging",
    title="Log failed logins",
    description="Adds a warning log line when a login fails. No behaviour change.",
    base={"auth/login.py": '''def login(users, username, password, check):
    user = users.get(username)
    if user is None or not check(password, user.password_hash):
        return None
    return user
'''},
    head={"auth/login.py": '''import logging

logger = logging.getLogger(__name__)


def login(users, username, password, check):
    user = users.get(username)
    if user is None or not check(password, user.password_hash):
        logger.warning("failed login for %r", username)
        return None
    return user
'''},
)

BASIC_CASES: tuple[Case, ...] = (
    PY_PAGINATION, PY_DISCOUNT, PY_PERMISSION, PY_PATH_TRAVERSAL, PY_RETRY, PY_SWAPPED_ARGS, PY_CENTS,
    JS_SORT, JS_AWAIT, JS_XSS, JS_MONTH, JS_ROLE,
    CLEAN_RENAME, CLEAN_VALIDATION, CLEAN_FLOAT_DECOY, CLEAN_JS_REFACTOR, CLEAN_PAIRS_DECOY, CLEAN_LOGGING,
)

from .cases_hard import HARD_CASES  # noqa: E402  (kept in their own modules for readability)
from .cases_rules import RULE_CASES  # noqa: E402

ALL_CASES: tuple[Case, ...] = BASIC_CASES + HARD_CASES + RULE_CASES
