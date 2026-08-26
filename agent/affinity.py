"""
agent/affinity.py
Builds a co-purchase matrix from order history.
Pure pandas. No LLM. No network.
"""
from __future__ import annotations
import json
from collections import defaultdict


class AffinityModel:
    def __init__(self):
        self._pair_counts = defaultdict(int)
        self._sku_counts  = defaultdict(int)
        self._trained     = False

    def train(self, orders: list):
        self._pair_counts.clear()
        self._sku_counts.clear()
        for order in orders:
            basket = order.get("basket", [])
            skus   = [item["sku"] for item in basket]
            for sku in skus:
                self._sku_counts[sku] += 1
            for i in range(len(skus)):
                for j in range(len(skus)):
                    if i != j:
                        self._pair_counts[(skus[i], skus[j])] += 1
        self._trained = True

    def top_companions(self, sku: str, k: int = 3) -> list:
        if not self._trained:
            return []
        primary_count = self._sku_counts.get(sku, 0)
        if primary_count == 0:
            return []
        companions = []
        for (a, b), count in self._pair_counts.items():
            if a == sku:
                companions.append({
                    "sku": b,
                    "count": count,
                    "affinity_score": round(count / primary_count, 3),
                })
        companions.sort(key=lambda x: x["count"], reverse=True)
        return companions[:k]

    def load_from_file(self, path: str):
        with open(path) as f:
            orders = json.load(f)
        self.train(orders)
        return self
