"""Hard per-run spending caps: Composio Instant tool calls (InstantBudget) and direct Exa calls (ExaBudget).

Instant toolkits bill per call from the project's prepaid balance. Every Composio
tool call reserves its listed price first; when that would push spend past
INSTANT_BUDGET_USD the call is skipped and the caller keeps its free result.
Spend is tracked as the charge Composio reports (`instant_charge.amount`) when
present, otherwise as the listed price (an estimate).
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections import Counter

log = logging.getLogger(__name__)

# Listed Instant prices in USD per call. Unknown tools use DEFAULT_PRICE.
LISTED_PRICES = {"FIRECRAWL_SCRAPE": 0.003, "EXA_SEARCH": 0.007, "TAVILY_SEARCH": 0.008}
DEFAULT_PRICE = 0.01

# Which app the current thread is researching, so spend can be reported per app.
current_app: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_app", default=None)


class BudgetExceeded(RuntimeError):
    """The call was not made: it would exceed the budget (or the budget is 0, which disables the provider)."""


def _empty() -> dict:
    return {"calls": 0, "by_tool": Counter(), "est_cost_usd": 0.0, "charged_usd": 0.0}


class InstantBudget:
    label, env_var = "Composio Instant", "INSTANT_BUDGET_USD"
    listed_prices, default_price = LISTED_PRICES, DEFAULT_PRICE

    def __init__(self, budget_usd: float, prices: dict[str, float] | None = None):
        self.budget_usd = budget_usd
        self.prices = {**self.listed_prices, **(prices or {})}
        self._lock = threading.Lock()
        self._reserved = 0.0
        self._total = _empty()
        self._by_app: dict[str, dict] = {}
        self._cap_logged = False

    def price(self, tool: str) -> float:
        return self.prices.get(tool, self.default_price)

    def _spent(self) -> float:
        return self._total["charged_usd"] + self._total["est_cost_usd"] + self._reserved

    def reserve(self, tool: str) -> None:
        """Reserve the listed price before a call, or raise BudgetExceeded."""
        price = self.price(tool)
        with self._lock:
            if self.budget_usd <= 0 or self._spent() + price > self.budget_usd + 1e-12:
                if not self._cap_logged:
                    self._cap_logged = True
                    why = f"disabled ({self.env_var}=0)" if self.budget_usd <= 0 else (
                        f"cap ${self.budget_usd:.2f} reached (spent/est ${self._spent():.4f})")
                    log.warning("%s %s; using free results for the rest of the run", self.label, why)
                raise BudgetExceeded(f"{self.label} budget {self.budget_usd} would be exceeded by {tool}")
            self._reserved += price

    def release(self, tool: str) -> None:
        """Undo a reservation for a call that did not run (or was not billed)."""
        with self._lock:
            self._reserved = max(0.0, self._reserved - self.price(tool))

    def record(self, tool: str, charged: float | None) -> None:
        """Settle a reservation after the call ran: actual charge if reported, else listed price."""
        price = self.price(tool)
        app = current_app.get()
        with self._lock:
            self._reserved = max(0.0, self._reserved - price)
            targets = [self._total] + ([self._by_app.setdefault(app, _empty())] if app else [])
            for t in targets:
                t["calls"] += 1
                t["by_tool"][tool] += 1
                if charged is None:
                    t["est_cost_usd"] += price
                else:
                    t["charged_usd"] += charged

    @staticmethod
    def _export(stats: dict) -> dict:
        return {
            "instant_calls": stats["calls"],
            "instant_calls_by_tool": dict(stats["by_tool"]),
            "instant_est_cost_usd": round(stats["est_cost_usd"], 6),
            "instant_charged_usd": round(stats["charged_usd"], 6),
        }

    def _budget_field(self) -> dict:
        return {"instant_budget_usd": self.budget_usd}

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._export(self._total), **self._budget_field()}

    def app_stats(self, app: str) -> dict:
        with self._lock:
            return self._export(self._by_app.get(app) or _empty())


# Listed Exa prices in USD: /search with up to 10 results (type auto, page text included),
# and /contents per page of text.
EXA_LISTED_PRICES = {"search": 0.007, "contents": 0.001}


class ExaBudget(InstantBudget):
    """The same reserve-or-skip cap, tracked separately for direct Exa API calls.

    `tool` is the endpoint (search / contents). A reported `costDollars.total` is recorded as
    `charged`; when Exa omits it the listed price is used. Both are estimates, not an invoice,
    so exa_cost_usd is their sum and exa_listed_estimate_usd shows the listed-price part.
    """

    label, env_var = "Exa", "EXA_BUDGET_USD"
    listed_prices, default_price = EXA_LISTED_PRICES, EXA_LISTED_PRICES["search"]

    @staticmethod
    def _export(stats: dict) -> dict:
        return {
            "exa_calls": stats["calls"],
            "exa_calls_by_endpoint": dict(stats["by_tool"]),
            "exa_cost_usd": round(stats["charged_usd"] + stats["est_cost_usd"], 6),
            "exa_listed_estimate_usd": round(stats["est_cost_usd"], 6),
        }

    def _budget_field(self) -> dict:
        return {"exa_budget_usd": self.budget_usd}


def parse_charge(resp: object) -> float | None:
    """`resp.instant_charge.amount` as a float, if Composio reported one."""
    charge = getattr(resp, "instant_charge", None)
    amount = charge.get("amount") if isinstance(charge, dict) else getattr(charge, "amount", None)
    try:
        return float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return None
