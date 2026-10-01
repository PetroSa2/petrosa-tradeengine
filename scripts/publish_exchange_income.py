#!/usr/bin/env python3
"""Dry-run or apply exchange income snapshots for a UTC date range."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta

from tradeengine.exchange.binance import BinanceFuturesExchange
from tradeengine.services.data_manager_client import DataManagerClient
from tradeengine.services.exchange_daily_publisher import (
    ExchangeDailyPublisher,
    payload_hash,
)


async def main(args: argparse.Namespace) -> None:
    exchange = BinanceFuturesExchange()
    await exchange.initialize()
    publisher = ExchangeDailyPublisher(exchange, DataManagerClient())
    current = date.fromisoformat(args.start)
    end = date.fromisoformat(args.to)
    while current <= end:
        day = current.isoformat()
        payload = await publisher.publish_day(day, is_final=True, apply=args.apply)
        print(
            json.dumps(
                {"day": day, "payload_hash": payload_hash(payload), "payload": payload},
                sort_keys=True,
            )
        )
        current += timedelta(days=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="to", required=True)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args()))
