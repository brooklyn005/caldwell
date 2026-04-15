from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database.models import SimClock

# Caldwell uses Year 0, Month 1, Day 1 on an American calendar structure
DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


class SimulationClock:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create(self) -> SimClock:
        clock = self.db.query(SimClock).first()
        if not clock:
            clock = SimClock(
                current_day=1,
                current_tick=0,
                is_running=False,
                sim_year=0,
                sim_month=1,
                sim_day_of_month=1,
            )
            self.db.add(clock)
            self.db.commit()
        return clock

    def advance_one_day(self) -> dict:
        """Advance the sim clock by one day and return the new date info."""
        clock = self.get_or_create()
        clock.current_day += 1
        clock.current_tick += 1
        clock.last_tick_at = datetime.now(timezone.utc)

        # Advance calendar
        clock.sim_day_of_month += 1
        days_in_current_month = DAYS_IN_MONTH[clock.sim_month]
        if clock.sim_day_of_month > days_in_current_month:
            clock.sim_day_of_month = 1
            clock.sim_month += 1
            if clock.sim_month > 12:
                clock.sim_month = 1
                clock.sim_year += 1

        self.db.commit()
        return self.current_date_dict(clock)

    def start(self):
        clock = self.get_or_create()
        clock.is_running = True
        if not clock.started_at:
            clock.started_at = datetime.now(timezone.utc)
        self.db.commit()

    def stop(self):
        clock = self.get_or_create()
        clock.is_running = False
        self.db.commit()

    def is_running(self) -> bool:
        clock = self.get_or_create()
        return clock.is_running

    def current_date_dict(self, clock: SimClock = None) -> dict:
        if clock is None:
            clock = self.get_or_create()
        return {
            "year": clock.sim_year,
            "month": clock.sim_month,
            "month_name": MONTH_NAMES[clock.sim_month],
            "day_of_month": clock.sim_day_of_month,
            "total_days": clock.current_day,
            "total_ticks": clock.current_tick,
            "is_running": clock.is_running,
            "display": (
                f"Year {clock.sim_year}, "
                f"{MONTH_NAMES[clock.sim_month]} {clock.sim_day_of_month}"
            ),
        }
