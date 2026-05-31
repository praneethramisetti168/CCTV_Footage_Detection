"""
POS Transaction Correlator.

Loads pos_transactions.csv on startup and provides:
  - conversion_rate(store_id): % of visitor sessions with a POS transaction
    in the 5-minute window after they entered the billing zone.
  - detect_billing_abandon(visitor_id, billing_exit_time): True if visitor
    left billing zone without a matching transaction.

Correlation logic (per challenge spec):
  A visitor who was in the billing zone in the 5-minute window BEFORE
  a transaction timestamp counts as a converted visitor for that session.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Correlation window: visitor in billing zone within N minutes before POS txn
CORRELATION_WINDOW_MINUTES = 5


class POSCorrelator:
    def __init__(self) -> None:
        # List of (timestamp_utc, store_id, basket_value)
        self._transactions: List[Dict] = []
        self._loaded = False

    def load(self, csv_path: str) -> None:
        """Load the normalised pos_transactions.csv."""
        if not os.path.isfile(csv_path):
            logger.warning("POS CSV not found at %s — conversion rate will be 0", csv_path)
            return

        loaded = 0
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts_str = row.get("timestamp", "")
                        # Parse ISO-8601 — strip trailing Z if present
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        self._transactions.append({
                            "transaction_id": row.get("transaction_id", ""),
                            "store_id": row.get("store_id", ""),
                            "timestamp": ts,
                            "basket_value_inr": float(row.get("basket_value_inr", 0) or 0),
                        })
                        loaded += 1
                    except Exception as e:
                        logger.debug("Skipping POS row: %s", e)
        except Exception as e:
            logger.error("Failed to load POS CSV: %s", e)
            return

        self._loaded = True
        logger.info("POS correlator loaded %d transactions from %s", loaded, csv_path)

    def get_transactions_for_store(self, store_id: str) -> List[Dict]:
        return [t for t in self._transactions if t["store_id"] == store_id]

    def compute_conversion_rate(
        self,
        store_id: str,
        billing_sessions: List[Dict],  # [{visitor_id, billing_entry_time (datetime)}]
        total_unique_visitors: int,
    ) -> float:
        """
        For each POS transaction, check if any visitor was in billing zone
        in the 5-minute window before the transaction. Those visitors are converted.
        """
        if total_unique_visitors == 0:
            return 0.0

        txns = self.get_transactions_for_store(store_id)
        if not txns:
            logger.debug("No POS transactions for store %s", store_id)
            return 0.0

        converted_visitors: Set[str] = set()
        window = timedelta(minutes=CORRELATION_WINDOW_MINUTES)

        for txn in txns:
            txn_ts = txn["timestamp"]
            for session in billing_sessions:
                billing_time = session.get("billing_entry_time")
                if billing_time is None:
                    continue
                # Ensure timezone-aware
                if billing_time.tzinfo is None:
                    billing_time = billing_time.replace(tzinfo=timezone.utc)
                # Visitor in billing zone within 5 min before transaction
                if txn_ts - window <= billing_time <= txn_ts:
                    converted_visitors.add(session["visitor_id"])

        rate = len(converted_visitors) / total_unique_visitors
        logger.debug(
            "Conversion: %d converted / %d visitors = %.3f",
            len(converted_visitors), total_unique_visitors, rate,
        )
        return round(rate, 4)

    def is_converted(
        self,
        store_id: str,
        visitor_id: str,
        billing_entry_time: datetime,
    ) -> bool:
        """Check if a specific visitor session converted (used for BILLING_QUEUE_ABANDON)."""
        txns = self.get_transactions_for_store(store_id)
        window = timedelta(minutes=CORRELATION_WINDOW_MINUTES)
        if billing_entry_time.tzinfo is None:
            billing_entry_time = billing_entry_time.replace(tzinfo=timezone.utc)
        for txn in txns:
            if txn["timestamp"] - window <= billing_entry_time <= txn["timestamp"]:
                return True
        return False

    def get_total_revenue(self, store_id: str) -> float:
        return sum(
            t["basket_value_inr"]
            for t in self._transactions
            if t["store_id"] == store_id
        )

    def get_transaction_count(self, store_id: str) -> int:
        return sum(1 for t in self._transactions if t["store_id"] == store_id)


# Singleton
pos_correlator = POSCorrelator()
