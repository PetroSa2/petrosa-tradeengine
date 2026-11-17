from tradeengine.dispatcher import _signal_outcome


def test_signal_outcomes_cover_terminal_dispatch_results():
    cases = {
        "order_accepted": {
            "status": "executed",
            "execution_result": {"status": "filled"},
        },
        "order_rejected": {
            "status": "exchange_failed",
            "execution_result": {"status": "rejected"},
        },
        "order_error": {
            "status": "exchange_failed",
            "execution_result": {"status": "error"},
        },
        "risk_rejected_portfolio_exposure": {
            "status": "rejected",
            "reason": "portfolio_exposure",
            "rejection_source": "risk_check",
        },
        "skip_symbol_not_allowed": {
            "status": "rejected",
            "reason": "symbol_not_allowed",
            "rejection_source": "whitelist",
        },
        "skip_halted": {
            "status": "rejected",
            "reason": "manual_halt",
            "rejection_source": "risk_check",
        },
        "skip_stale": {
            "status": "rejected",
            "reason": "accumulation_cooldown",
            "rejection_source": "stale_signal",
        },
        "skip_duplicate": {"status": "duplicate"},
        "skip_hold": {"status": "hold"},
        "skip_restricted": {"status": "aborted"},
        "timeout": {"status": "lock_unavailable"},
        "error": {"status": "error"},
    }

    for expected, result in cases.items():
        assert _signal_outcome(result) == expected
