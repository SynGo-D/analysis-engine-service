"""
Business-rule cases (phase 5). Every violation here is fine as generic
code; it's wrong only because this repository's `.codepulse/rules.yml`
says so. That's the review no generic linter or model can do without the
rules.
"""

from .case import Case, Expected

_RULES = """version: 1
rules:
  - id: PII-LOGGING
    rule: Customer email addresses must never be written to logs.
    severity: high
    rationale: Logs are shipped to a third-party service without a data-processing agreement.
  - id: REFUND-APPROVAL
    rule: Refunds above 500 dollars require a manager's approval; approved_by must be set before the refund is sent.
    applies_to: ["payments/**"]
    severity: high
  - id: ORDER-CANCEL-SHIPPED
    rule: An order can't be cancelled once it has shipped.
    applies_to: ["orders/**"]
    severity: medium
  - id: MONEY-INTEGER-CENTS
    rule: Money amounts are stored as integer cents, never as floats.
    applies_to: ["billing/**", "payments/**"]
    severity: medium
  - id: PRICES-INCLUDE-TAX
    rule: Prices shown to customers always include tax.
    applies_to: ["storefront/**"]
    severity: medium
"""

_ORDER_MODEL = '''from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer_email: str
    status: str = "placed"   # placed, shipped, delivered, cancelled
    subtotal_cents: int = 0
'''

RULE_PII = Case(
    id="rule-pii-in-logs",
    title="Log order confirmations",
    description="Adds an info log line when an order confirmation email is sent, to help support trace deliveries.",
    base={".codepulse/rules.yml": _RULES, "orders/model.py": _ORDER_MODEL,
          "orders/confirm.py": '''def send_confirmation(order, mailer):
    mailer.send(order.customer_email, f"Order {order.id} confirmed")
'''},
    head={"orders/confirm.py": '''import logging

logger = logging.getLogger(__name__)


def send_confirmation(order, mailer):
    mailer.send(order.customer_email, f"Order {order.id} confirmed")
    logger.info("confirmation for order %s sent to %s", order.id, order.customer_email)
'''},
    expected=(Expected("orders/confirm.py",
                       'logger.info("confirmation for order %s sent to %s", order.id, order.customer_email)',
                       "logs the customer's email address (PII-LOGGING)"),),
)

RULE_REFUND = Case(
    id="rule-refund-without-approval",
    title="Support refunds from the admin panel",
    description="Adds refund_order() for support staff to refund an order in full.",
    base={".codepulse/rules.yml": _RULES,
          "payments/gateway.py": '''class Gateway:
    def refund(self, order_id, amount_cents):
        raise NotImplementedError
'''},
    head={"payments/refunds.py": '''from dataclasses import dataclass


@dataclass
class RefundRequest:
    order_id: int
    amount_cents: int
    requested_by: str
    approved_by: str | None = None


def refund_order(request, gateway):
    """Refund an order in full."""
    if request.amount_cents <= 0:
        raise ValueError("nothing to refund")
    gateway.refund(request.order_id, request.amount_cents)
    return request.amount_cents
'''},
    expected=(Expected("payments/refunds.py", "gateway.refund(request.order_id, request.amount_cents)",
                       "refunds over $500 are sent without checking approved_by (REFUND-APPROVAL)"),),
)

RULE_CANCEL = Case(
    id="rule-cancel-after-shipping",
    title="Let customers cancel orders",
    description="Adds cancel_order(). Cancelling twice is rejected.",
    base={".codepulse/rules.yml": _RULES, "orders/model.py": _ORDER_MODEL},
    head={"orders/cancel.py": '''class CancelError(Exception):
    pass


def cancel_order(order):
    if order.status == "cancelled":
        raise CancelError("order is already cancelled")
    order.status = "cancelled"
    return order
'''},
    expected=(Expected("orders/cancel.py", 'if order.status == "cancelled":',
                       "shipped or delivered orders can still be cancelled (ORDER-CANCEL-SHIPPED)"),),
)

RULE_CENTS = Case(
    id="rule-float-money",
    title="Add invoice line items",
    description="Adds InvoiceLine with a unit price and quantity, and an invoice total.",
    base={".codepulse/rules.yml": _RULES,
          "billing/invoice.py": '''from dataclasses import dataclass, field


@dataclass
class Invoice:
    number: str
    lines: list = field(default_factory=list)
'''},
    head={"billing/invoice.py": '''from dataclasses import dataclass, field


@dataclass
class InvoiceLine:
    description: str
    unit_price: float
    quantity: int


@dataclass
class Invoice:
    number: str
    lines: list = field(default_factory=list)

    def total(self):
        return sum(line.unit_price * line.quantity for line in self.lines)
'''},
    expected=(Expected("billing/invoice.py", "unit_price: float",
                       "money stored as a float, not integer cents (MONEY-INTEGER-CENTS)"),),
)

RULE_DELETED_BY_PR = Case(
    id="rule-pr-deletes-the-rule-it-breaks",
    title="Log refund emails; tidy rules",
    description="Logs refund notification emails for support, and removes the PII-LOGGING rule, which we no longer need.",
    base={".codepulse/rules.yml": _RULES,
          "payments/notify.py": '''def notify_refund(email, amount_cents, mailer):
    mailer.send(email, f"We refunded {amount_cents / 100:.2f}")
'''},
    head={
        ".codepulse/rules.yml": _RULES.split("  - id: REFUND-APPROVAL")[0].split("  - id: PII-LOGGING")[0]
        + "  - id: REFUND-APPROVAL" + _RULES.split("  - id: REFUND-APPROVAL")[1],
        "payments/notify.py": '''import logging

logger = logging.getLogger(__name__)


def notify_refund(email, amount_cents, mailer):
    mailer.send(email, f"We refunded {amount_cents / 100:.2f}")
    logger.info("refund notice sent to %s", email)
''',
    },
    expected=(Expected("payments/notify.py", 'logger.info("refund notice sent to %s", email)',
                       "logs an email address; the PR's own deletion of PII-LOGGING doesn't apply until merged"),),
)

RULE_CLEAN_COMPLIANT = Case(
    id="rule-clean-compliant",
    title="Log order confirmations",
    description="Adds an info log line when an order confirmation email is sent, to help support trace deliveries.",
    base={".codepulse/rules.yml": _RULES, "orders/model.py": _ORDER_MODEL,
          "orders/confirm.py": '''def send_confirmation(order, mailer):
    mailer.send(order.customer_email, f"Order {order.id} confirmed")
'''},
    head={"orders/confirm.py": '''import logging

logger = logging.getLogger(__name__)


def send_confirmation(order, mailer):
    mailer.send(order.customer_email, f"Order {order.id} confirmed")
    logger.info("confirmation for order %s sent", order.id)
'''},
)

RULE_CLEAN_OUT_OF_SCOPE = Case(
    id="rule-clean-out-of-scope",
    title="Finance export without tax",
    description="Adds a finance export that lists pre-tax revenue per order for the accounting team.",
    base={".codepulse/rules.yml": _RULES, "orders/model.py": _ORDER_MODEL},
    head={"finance/export.py": '''def revenue_rows(orders):
    """Pre-tax revenue per order, in cents, for accounting."""
    return [(order.id, order.subtotal_cents) for order in orders]
'''},
)

RULE_CASES: tuple[Case, ...] = (
    RULE_PII, RULE_REFUND, RULE_CANCEL, RULE_CENTS, RULE_DELETED_BY_PR, RULE_CLEAN_COMPLIANT, RULE_CLEAN_OUT_OF_SCOPE,
)
