"""ORM models package.

Import models here so they are registered on Base.metadata
when init_db() is called.
"""

from webapp.models.market_data import EtfDailyBar, EtfMinuteBar  # noqa: F401
