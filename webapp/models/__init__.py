"""ORM models package.

Import models here so they are registered on Base.metadata
when init_db() is called.
"""

from webapp.models.classification import ClassificationRule  # noqa: F401
from webapp.models.constraint_config import ConstraintConfig  # noqa: F401
from webapp.models.macro import MacroDaily, MacroMonthly  # noqa: F401
from webapp.models.market_data import EtfDailyBar, EtfMinuteBar  # noqa: F401
from webapp.models.strategy_run import StrategyRun  # noqa: F401
from webapp.models.universe import UniverseItem  # noqa: F401