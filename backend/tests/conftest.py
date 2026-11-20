"""测试夹具：每个用例前把内存仓库恢复成种子数据，避免用例互相影响。"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.seed_pipeline import PIPELINE_ROWS
from app.store import store


@pytest.fixture(autouse=True)
def fresh_store():
    for name in list(store._tables):
        store._tables[name] = []
    for name, rows in SEED_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]
    for name, rows in PIPELINE_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]
    yield
