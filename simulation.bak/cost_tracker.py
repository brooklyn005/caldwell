from datetime import datetime, timezone
from sqlalchemy.orm import Session
from config import settings
from database.models import CostLog, DailySpend


class CostTracker:
    """
    Tracks daily API spend across Haiku and DeepSeek.
    Hard-stops the simulation when daily_budget_usd is reached.
    All cost in USD.
    """

    def __init__(self, db: Session):
        self.db = db

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_or_create_daily(self) -> DailySpend:
        today = self._today()
        row = self.db.query(DailySpend).filter(DailySpend.date_utc == today).first()
        if not row:
            row = DailySpend(date_utc=today, total_usd=0.0, is_paused_by_budget=False)
            self.db.add(row)
            self.db.commit()
        return row

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Record token usage and return the cost of this call.
        In local mode, tokens are tracked but cost is always 0.
        """
        if settings.ai_mode in ("local", "mixed"):
            return 0.0
        if model == "haiku":
            cost = (input_tokens / 1_000_000 * settings.haiku_input_cost +
                    output_tokens / 1_000_000 * settings.haiku_output_cost)
        else:
            cost = (input_tokens / 1_000_000 * settings.deepseek_input_cost +
                    output_tokens / 1_000_000 * settings.deepseek_output_cost)

        log = CostLog(
            date_utc=self._today(),
            ai_model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.db.add(log)

        daily = self._get_or_create_daily()
        daily.total_usd = round(daily.total_usd + cost, 6)
        self.db.commit()
        return cost

    def today_spend(self) -> float:
        row = self._get_or_create_daily()
        return round(row.total_usd, 4)

    def budget_remaining(self) -> float:
        return max(0.0, round(settings.daily_budget_usd - self.today_spend(), 4))

    def is_budget_exhausted(self) -> bool:
        return self.today_spend() >= settings.daily_budget_usd

    def mark_paused(self):
        daily = self._get_or_create_daily()
        daily.is_paused_by_budget = True
        self.db.commit()

    def status_dict(self) -> dict:
        spent = self.today_spend()
        budget = settings.daily_budget_usd
        return {
            "date": self._today(),
            "spent_usd": spent,
            "budget_usd": budget,
            "remaining_usd": max(0.0, round(budget - spent, 4)),
            "pct_used": round((spent / budget) * 100, 1),
            "exhausted": spent >= budget,
        }
