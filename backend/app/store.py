"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
归属受控批次相关的表（batchscope_*）支持事务：事务内任一步失败，受管表整体回滚。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 归属受控模块涉及多表同事务，用一把可重入锁串行化所有写操作。
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def ensure_table(self, name: str, seed_rows: list[dict[str, Any]]) -> None:
        """幂等挂载一张新表：已存在时保留运行期数据，不存在时灌入种子。"""
        with self._lock:
            if name not in self._tables:
                self._tables[name] = [dict(row) for row in seed_rows]

    def reset_to_seed(self) -> None:
        """把仓库恢复到初始种子状态（供验收测试在用例之间隔离数据）。"""
        with self._lock:
            self._tables = {
                name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
            }

    @contextmanager
    def transaction(self, managed_tables: list[str]) -> Iterator[None]:
        """受管表事务：正常结束提交，抛出异常时把受管表恢复到进入前的快照。

        爬虫引擎生成归属受控数据集时，数据集与目标站点执行快照必须同生共死，
        任一步失败都靠这里整体回滚，避免留下半条记录。
        """
        with self._lock:
            snapshot = {
                name: deepcopy(self._tables.get(name, []))
                for name in managed_tables
            }
            try:
                yield
            except Exception:
                for name, rows in snapshot.items():
                    self._tables[name] = rows
                raise

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
