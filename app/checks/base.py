from abc import ABC, abstractmethod
from app.models import CheckResult


class BaseCheck(ABC):
    name: str
    display_name: str
    max_score: int
    tier: int = 2  # 1=primary signals, 2=important, 3=technical detail

    def build_result(self, **kwargs) -> CheckResult:
        """Create a CheckResult with tier copied from this check class."""
        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            tier=self.tier,
            **kwargs,
        )

    @abstractmethod
    async def run(self, domain: str, **kwargs) -> CheckResult:
        pass
