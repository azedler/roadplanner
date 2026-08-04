"""Regression check that interactive timeouts preserve the fallback budget."""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")

needle = '''err.code == "timeout"\n                                and model_index == 0\n                                and len(models) > 1'''
assert needle in SOURCE
assert "Preserve the\n                                # remaining deadline for the configured fallback" in SOURCE

print("Gemini timeout fallback contract tests passed.")

# Live report: "Gemini hat nicht rechtzeitig geantwortet" on
# assistant_prepare. A single call used to be hard-capped at 40 s even
# though the configured budget was far larger, so a big ChangeSet draft
# could never finish and the retry got an even smaller slice.
assert "min(40.0, remaining)" not in SOURCE, (
    "a single call must be allowed to use the configured budget"
)
assert "timeout_seconds=max(10.0, remaining - reserve)" in SOURCE
assert "reserve = 15.0 if retries_left > 0 and remaining > 70 else 0.0" in SOURCE, (
    "room for a retry is reserved only while there is plenty of budget left"
)

CONST = Path("custom_components/roadplanner_mcp/const.py").read_text(encoding="utf-8")
assert "DEFAULT_ASSISTANT_REQUEST_TIMEOUT = 120" in CONST


def verify_budget_split() -> None:
    """The arithmetic the client uses, checked directly."""
    def call_timeout(remaining: float, retries_left: int) -> float:
        reserve = 15.0 if retries_left > 0 and remaining > 70 else 0.0
        return max(10.0, remaining - reserve)

    # Fresh 120 s budget with one retry left: the first call may run 105 s
    # instead of the old 40 s ceiling.
    assert call_timeout(120.0, 1) == 105.0
    # Last attempt uses everything that is left.
    assert call_timeout(60.0, 0) == 60.0
    # A nearly exhausted budget still yields a usable, bounded slice.
    assert call_timeout(4.0, 1) == 10.0


verify_budget_split()
print("Gemini request budget tests passed.")
