"""Hard per-run spending cap for Composio Instant tool calls.

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
    """The call was not made: it would exceed the Instant budget (or Instant is disabled)."""


def _empty() -> dict:
    return {"calls": 0, "by_tool": Counter(), "est_cost_usd": 0.0, "charged_usd": 0.0}


class InstantBudget:
    def __init__(self, budget_usd: float, prices: dict[str, float] | None = None):
        self.budget_usd = budget_usd
        self.prices = {**LISTED_PRICES, **(prices or {})}
        self._lock = threading.Lock()
        self._reserved = 0.0
        self._total = _empty()
        self._by_app: dict[str, dict] = {}
        self._cap_logged = False

    def price(self, tool: str) -> float:
        return self.prices.get(tool, DEFAULT_PRICE)

    def _spent(self) -> float:
        return self._total["charged_usd"] + self._total["est_cost_usd"] + self._reserved

    def reserve(self, tool: str) -> None:
        """Reserve the listed price before a call, or raise BudgetExceeded."""
        price = self.price(tool)
        with self._lock:
            if self.budget_usd <= 0 or self._spent() + price > self.budget_usd + 1e-12:
                if not self._cap_logged:
                    self._cap_logged = True
                    why = "disabled (INSTANT_BUDGET_USD=0)" if self.budget_usd <= 0 else (
                        f"cap ${self.budget_usd:.2f} reached (spent/est ${self._spent():.4f})")
                    log.warning("Composio Instant %s; using free results for the rest of the run", why)
                raise BudgetExceeded(f"Instant budget {self.budget_usd} would be exceeded by {tool}")
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

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._export(self._total), "instant_budget_usd": self.budget_usd}

    def app_stats(self, app: str) -> dict:
        with self._lock:
            return self._export(self._by_app.get(app) or _empty())


def parse_charge(resp: object) -> float | None:
    """`resp.instant_charge.amount` as a float, if Composio reported one."""
    charge = getattr(resp, "instant_charge", None)
    amount = charge.get("amount") if isinstance(charge, dict) else getattr(charge, "amount", None)
    try:
        return float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return None
