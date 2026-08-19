"""
Configuration settings for Petrosa Trading Engine
"""

from typing import Any

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    # Environment
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # API Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False

    # Binance API Configuration
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_testnet: bool = True
    binance_base_url: str = "https://testnet.binance.vision"

    # MongoDB Configuration (from Kubernetes configmap and secret)
    mongodb_uri: str | None = None  # From secret: petrosa-sensitive-credentials
    mongodb_database: str | None = None  # From configmap: petrosa-common-config

    # JWT Configuration
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Trading Configuration
    simulation_enabled: bool = True
    max_position_size_pct: float = 0.1  # 10%
    max_daily_loss_pct: float = 0.05  # 5%
    max_portfolio_exposure_pct: float = 0.8  # 80%
    risk_management_enabled: bool = True

    # AC4 of #424 (2026-05-30 OCO incident): minimum stop-loss distance
    # from the live market, in percent. If an adjusted SL would land
    # within this band of market, the PERCENT_PRICE adjuster refuses to
    # return a price — the dispatcher then emits a structured rejection
    # instead of placing a guaranteed-to-trigger stop. Default 6.0%
    # covers typical Binance Futures 4h candle moves (4-5% on alts) plus
    # headroom; can be tightened/relaxed per-deploy via env var.
    te_min_sl_distance_pct: float = 6.0

    # #534 (H6 of #977): WS-driven OCO completion nudge. When enabled, a
    # FILLED ORDER_TRADE_UPDATE on an SL/TP leg that belongs to a tracked OCO
    # pair wakes the _monitor_orders poll immediately instead of waiting for
    # the next 2s cycle. The poll remains the authoritative decision-maker and
    # backstop — WS only shortens detection latency, it never cancels/closes
    # directly, so there is no double-cancel risk. Default off until validated
    # on testnet. Rollback: unset TE_OCO_WS_WAKE_ENABLED (or set false).
    te_oco_ws_wake_enabled: bool = False

    # Redis Configuration (for caching)
    redis_url: str | None = None
    redis_password: str | None = None
    redis_db: int = 0

    # Monitoring Configuration
    prometheus_enabled: bool = True
    health_check_interval: int = 30

    # Distributed Lock Configuration
    lock_timeout_seconds: int = 60
    heartbeat_interval_seconds: int = 10

    # NATS Configuration
    nats_enabled: bool = False
    nats_url: str | None = None
    nats_servers: str | None = None
    # Align default with shared.constants default and TA bot publisher
    nats_topic_signals: str = "signals.trading.*"
    nats_topic_heartbeat: str = "cio.heartbeat"
    nats_topic_execution_events: str = "execution.events"

    # CIO Enforcement (Ticket #304 / P0 #1)
    enforce_cio_audit: bool = True  # Default to True for maximum safety

    # API Configuration (for uvicorn)

    api_port: int = 8000

    # MySQL Configuration (legacy support)
    mysql_uri: str | None = None

    # Position Reconciliation (FR65)
    position_reconciliation_enabled: bool = True
    position_reconciliation_interval_seconds: int = 60
    # #540: decouple the naked-position watchdog from `simulation_enabled`.
    # The boot gate historically required `not simulation_enabled`, but the
    # live deployment sets TE_NAKED_POSITION_REMEDIATION_MODE while leaving
    # SIMULATION_ENABLED at its True default — so the reconciler/remediator
    # were never constructed and `arm_only` was a silent no-op while real
    # orders filled on Binance (18 naked positions, zero remediation cycles).
    # A naked-position safety net must run whenever real orders can be
    # placed, regardless of the sim flag. Default False = start the watchdog
    # whenever `position_reconciliation_enabled` is True. Set to True to
    # restore the legacy sim-gated behavior (skip when simulation_enabled).
    position_reconciliation_requires_live_only: bool = False

    # AC1 (#459 — 446-C): ExchangeTruthStore read-path feature flag
    # off = legacy paths unchanged; shadow = log divergence only; on = risk reads from exchange
    te_exchange_truth_store_enabled: str = "off"

    # tradeengine#533 (H5 of #977): persist HeartbeatMonitor.restricted_mode
    # across process restarts. "off" keeps the legacy in-memory-only behavior;
    # "on" durably records restricted-mode transitions to MongoDB and restores
    # them at boot (unverifiable state fails closed -> restricted). Default
    # "off" preserves existing behavior until the operator promotes it.
    te_heartbeat_persist_enabled: str = "off"

    # tradeengine#529: backfill exchange-sourced fill audit fields (commission,
    # commissionAsset, realizedPnl) for FILLED Binance USDⓈ-M Futures orders.
    # The synchronous futures_create_order response omits the per-trade fills[]
    # array (unlike Spot), so fee/fee_asset/pnl are unavailable when the
    # execution.events fill event is emitted. When enabled, the exchange makes a
    # best-effort GET /fapi/v1/userTrades (futures_account_trades) call for the
    # filled order and folds the real values into the emitted audit event. The
    # call is best-effort — any failure is swallowed and the order path is never
    # affected. Set TE_FILL_AUDIT_ENRICHMENT_ENABLED=false to disable the extra
    # per-fill REST call per deploy.
    te_fill_audit_enrichment_enabled: bool = True

    # #445: exchange-authoritative naked-position remediation.
    # Modes: "off" (read-only, no writes — detection-only), "dry_run"
    # (log intended actions, no writes), "arm_only" (re-arm protective
    # stops but never flatten), "arm_or_flatten" (full AC2 — re-arm with
    # fallback flatten after grace window).
    # #500: default is "dry_run" (NOT "off"). A fresh deploy that
    # silently ran "off" only incremented the detection counter and took
    # no corrective write action — a "watchdog that never enforces" (see
    # the 2026-07-16 naked-position incident). "dry_run" preserves the
    # no-write safety of "off" for a first boot while making the
    # remediator's intended actions observable in logs, so a missing
    # TE_NAKED_POSITION_REMEDIATION_MODE env can never leave the fleet
    # detection-only-and-blind. Operator promotes dry_run → arm_only →
    # arm_or_flatten after canary validation.
    naked_position_remediation_mode: str = "dry_run"
    # Grace window before fallback flatten kicks in (arm_or_flatten only).
    naked_position_flatten_grace_sec: int = 60
    # Fallback SL/TP distances (% from entry) when local strategy record
    # has no stored stop_loss_price/take_profit_price for a naked position.
    #
    # CRITICAL (2026-07-20 second-wave OCO-orphan incident): the SL fallback
    # MUST clear te_min_sl_distance_pct (the safety floor, default 6.0%).
    # A fallback below the floor is un-armable — the price-adjuster in
    # BinanceFuturesExchange.execute() returns (False, None, reason) for any
    # STOP inside the floor, the re-arm raises, and arm_or_flatten degrades to
    # "flatten everything after the grace window". The old 2.0% value was
    # hardwired to lose that fight. Keep this >= te_min_sl_distance_pct with a
    # small margin so the re-armed stop lands just outside the floor.
    # TP is not floor-constrained (TPs may sit near market), so its fallback
    # is unchanged.
    naked_position_fallback_sl_pct: float = 6.5
    naked_position_fallback_tp_pct: float = 4.0

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "allow",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Set default MongoDB URL if not provided
        if not self.mongodb_uri:
            from shared.constants import get_mongodb_connection_string

            self.mongodb_uri = get_mongodb_connection_string()

        # Set NATS configuration from constants
        from shared.constants import (
            NATS_ENABLED,
            NATS_TOPIC_EXECUTION_EVENTS,
            NATS_TOPIC_HEARTBEAT,
            NATS_TOPIC_SIGNALS,
            get_nats_connection_string,
        )

        self.nats_enabled = NATS_ENABLED
        if self.nats_enabled:
            self.nats_url = get_nats_connection_string()
            self.nats_servers = self.nats_url
        else:
            self.nats_servers = None

        # Ensure subjects align with shared.constants if env not set
        if not kwargs.get("nats_topic_signals"):
            self.nats_topic_signals = NATS_TOPIC_SIGNALS

        if not kwargs.get("nats_topic_heartbeat"):
            self.nats_topic_heartbeat = NATS_TOPIC_HEARTBEAT

        if not kwargs.get("nats_topic_execution_events"):
            self.nats_topic_execution_events = NATS_TOPIC_EXECUTION_EVENTS

    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment.lower() == "development"

    @property
    def is_testing(self) -> bool:
        """Check if running in testing environment"""
        return self.environment.lower() == "testing"

    def get_mongodb_connection_string(self) -> str:
        """Get MongoDB connection string"""
        from shared.constants import get_mongodb_connection_string

        return self.mongodb_uri or get_mongodb_connection_string()

    def validate_required_settings(self) -> None:
        """Validate that required settings are present"""
        if self.is_production:
            if not self.binance_api_key:
                raise ValueError("BINANCE_API_KEY is required in production")
            if not self.binance_api_secret:
                raise ValueError("BINANCE_API_SECRET is required in production")
            if not self.jwt_secret_key:
                raise ValueError("JWT_SECRET_KEY is required in production")
            if not self.mongodb_uri:
                raise ValueError("MONGODB_URI is required in production")


# Global settings instance
settings = Settings()
