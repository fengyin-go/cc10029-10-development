"""归属受控批次模块的种子数据与引导逻辑。

这里的数据独立于通用 SEED_ROWS：模块首次被服务加载时，通过 store.ensure_table
幂等挂载；同时给爬虫任务表补一条“无归属授权任务”，用于验证越权/无归属批次必须
被排除在团队可见的归属受控数据集之外。
"""
from __future__ import annotations

from typing import Any

from app.store import store

TEAM_A = "TEAM-A"
TEAM_B = "TEAM-B"

# 归属受控数据集对外稳定的输入/输出字段；版本切换时保持不变。
DATASET_INPUT_FIELDS = ["团队编号", "爬虫任务编号", "幂等键", "候选批次号清单"]
DATASET_OUTPUT_FIELDS = [
    "数据集编号",
    "团队编号",
    "爬虫任务编号",
    "版本号",
    "批次清单",
    "批次数",
    "快照编号",
    "回执编号",
    "生成时间",
]

TABLE_TEAMS = "batchscope_teams"
TABLE_TEAM_SITES = "batchscope_team_sites"
TABLE_OWNERSHIP = "batchscope_task_ownership"
TABLE_HANDOVERS = "batchscope_handovers"
TABLE_RUNS = "batchscope_runs"
TABLE_DATASETS = "batchscope_datasets"
TABLE_SNAPSHOTS = "batchscope_snapshots"

ALL_TABLES = [
    TABLE_TEAMS,
    TABLE_TEAM_SITES,
    TABLE_OWNERSHIP,
    TABLE_HANDOVERS,
    TABLE_RUNS,
    TABLE_DATASETS,
    TABLE_SNAPSHOTS,
]

_TEAMS = [
    {"id": 1, "team_id": TEAM_A, "team_name": "采集团队甲", "说明": "负责资讯类站点的爬虫任务"},
    {"id": 2, "team_id": TEAM_B, "team_name": "采集团队乙", "说明": "负责电商类站点的爬虫任务"},
]

_TEAM_SITES = [
    {"id": 1, "team_id": TEAM_A, "targetsite_id": 1, "site_code": "TARG-0001", "site_name": "采集目标样例1", "entry_url": "https://site-a1.example.com"},
    {"id": 2, "team_id": TEAM_A, "targetsite_id": 2, "site_code": "TARG-0002", "site_name": "采集目标样例2", "entry_url": "https://site-a2.example.com"},
    {"id": 3, "team_id": TEAM_B, "targetsite_id": 3, "site_code": "TARG-0003", "site_name": "采集目标样例3", "entry_url": "https://site-b1.example.com"},
]

_OWNERSHIP = [
    {"id": 1, "crawler_task_id": 1, "task_code": "CRAW-0001", "task_name": "爬虫引擎样例1",
     "owner_team_id": TEAM_A, "write_team_id": TEAM_A, "status": "归属中",
     "site_codes": ["TARG-0001", "TARG-0002"], "current_handover_id": None},
    {"id": 2, "crawler_task_id": 2, "task_code": "CRAW-0002", "task_name": "爬虫引擎样例2",
     "owner_team_id": TEAM_B, "write_team_id": TEAM_B, "status": "归属中",
     "site_codes": ["TARG-0003"], "current_handover_id": None},
    {"id": 3, "crawler_task_id": 3, "task_code": "CRAW-0003", "task_name": "爬虫引擎样例3",
     "owner_team_id": TEAM_A, "write_team_id": TEAM_A, "status": "归属中",
     "site_codes": ["TARG-0001"], "current_handover_id": None},
    # 任务 4 没有任何归属授权：它产生的运行批次不允许进入任何团队的数据集。
    {"id": 4, "crawler_task_id": 4, "task_code": "CRAW-0004", "task_name": "无归属授权爬虫任务",
     "owner_team_id": None, "write_team_id": None, "status": "无归属",
     "site_codes": ["TARG-0001"], "current_handover_id": None},
]

_HANDOVERS = [
    {"id": 1, "handover_no": "HAND-0001", "crawler_task_id": 3, "task_code": "CRAW-0003",
     "from_team_id": TEAM_B, "to_team_id": TEAM_A, "write_team_id": TEAM_A,
     "status": "已完成", "reason": "站点维护职责调整（历史交接样例）",
     "created_at": "2026-11-10T09:00:00", "completed_at": "2026-11-10T10:30:00"},
]

_RUNS = [
    {"id": 1, "run_no": "RUN-0001", "crawler_task_id": 1, "task_code": "CRAW-0001",
     "owner_team_id": TEAM_A, "started_by_team_id": TEAM_A, "site_code": "TARG-0001",
     "site_name": "采集目标样例1", "status": "已完成", "records": 120,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-11T08:00:00", "finished_at": "2026-11-11T08:40:00"},
    {"id": 2, "run_no": "RUN-0002", "crawler_task_id": 1, "task_code": "CRAW-0001",
     "owner_team_id": TEAM_A, "started_by_team_id": TEAM_A, "site_code": "TARG-0002",
     "site_name": "采集目标样例2", "status": "已完成", "records": 86,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-12T08:00:00", "finished_at": "2026-11-12T08:35:00"},
    {"id": 3, "run_no": "RUN-0003", "crawler_task_id": 2, "task_code": "CRAW-0002",
     "owner_team_id": TEAM_B, "started_by_team_id": TEAM_B, "site_code": "TARG-0003",
     "site_name": "采集目标样例3", "status": "已完成", "records": 210,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-12T09:00:00", "finished_at": "2026-11-12T09:50:00"},
    {"id": 4, "run_no": "RUN-0004", "crawler_task_id": 3, "task_code": "CRAW-0003",
     "owner_team_id": TEAM_A, "started_by_team_id": TEAM_A, "site_code": "TARG-0001",
     "site_name": "采集目标样例1", "status": "运行中", "records": 0,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-16T08:00:00", "finished_at": None},
    {"id": 5, "run_no": "RUN-0005", "crawler_task_id": 1, "task_code": "CRAW-0001",
     "owner_team_id": TEAM_A, "started_by_team_id": TEAM_A, "site_code": "TARG-0001",
     "site_name": "采集目标样例1", "status": "失败", "records": 0,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-13T08:00:00", "finished_at": "2026-11-13T08:12:00"},
    # 无归属授权任务产生的运行记录：任何团队的数据集都必须排除它。
    {"id": 6, "run_no": "RUN-0006", "crawler_task_id": 4, "task_code": "CRAW-0004",
     "owner_team_id": None, "started_by_team_id": None, "site_code": "TARG-0001",
     "site_name": "采集目标样例1", "status": "已完成", "records": 55,
     "conflict_resolved": False, "superseded_by": None, "handover_id": None,
     "started_at": "2026-11-14T08:00:00", "finished_at": "2026-11-14T08:20:00"},
]

_BOOTSTRAPPED = False


def bootstrap() -> None:
    """幂等挂载归属受控模块的全部表，并补齐无归属爬虫任务。"""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    with store.lock:
        store.ensure_table(TABLE_TEAMS, _TEAMS)
        store.ensure_table(TABLE_TEAM_SITES, _TEAM_SITES)
        store.ensure_table(TABLE_OWNERSHIP, _OWNERSHIP)
        store.ensure_table(TABLE_HANDOVERS, _HANDOVERS)
        store.ensure_table(TABLE_RUNS, _RUNS)
        store.ensure_table(TABLE_DATASETS, [])
        store.ensure_table(TABLE_SNAPSHOTS, [])
        crawler_rows = store.rows("crawler")
        if not any(int(row.get("id", 0)) == 4 for row in crawler_rows):
            crawler_rows.append({
                "id": 4,
                "status": "已停用",
                "pending": False,
                "abnormal": True,
                "任务编号": "CRAW-0004",
                "任务名称": "无归属授权爬虫任务",
                "关联站点": "采集目标样例1",
                "关联规则": "无",
                "并发线程": "2",
                "调度周期": "手动",
                "超时秒数": "30",
                "下发批次": "无授权",
            })
        _BOOTSTRAPPED = True


def next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def next_no(rows: list[dict[str, Any]], field: str, prefix: str, width: int = 4) -> str:
    used = [str(row.get(field, "")) for row in rows]
    index = len(rows) + 1
    while f"{prefix}-{index:0{width}d}" in used:
        index += 1
    return f"{prefix}-{index:0{width}d}"
