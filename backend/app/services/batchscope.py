"""归属受控爬虫批次数据集的领域服务。

权限边界（“归属看爬虫引擎，越权拒绝，只读账户不能改动”）：
- 所有写操作只允许当前写权限团队的读写账户执行；
- 只读账户、非归属团队一律 403；
- 无归属授权任务产生的批次不会进入任何团队的数据集。

一致性：
- 数据集与目标站点执行快照在同一事务内生成，任一失败整体回滚；
- 批次运行中发生归属切换并产生权限冲突时，下游只裁决出一份权限边界明确的完整批次；
- 同一幂等键重复提交只记一次，按成功回执返回。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app import batchscope_seed as seed
from app.batchscope_seed import (
    ALL_TABLES,
    DATASET_INPUT_FIELDS,
    DATASET_OUTPUT_FIELDS,
    TABLE_DATASETS,
    TABLE_HANDOVERS,
    TABLE_OWNERSHIP,
    TABLE_RUNS,
    TABLE_SNAPSHOTS,
    TABLE_TEAMS,
    TABLE_TEAM_SITES,
    next_id,
    next_no,
)
from app.store import store

ROLE_WRITE = "write"
ROLE_READONLY = "readonly"
VALID_ROLES = {ROLE_WRITE, ROLE_READONLY}

RUN_RUNNING = "运行中"
RUN_DONE = "已完成"
RUN_FAILED = "失败"

OWNERSHIP_ACTIVE = "归属中"
OWNERSHIP_HANDOVER = "交接中"
OWNERSHIP_NONE = "无归属"

HANDOVER_PENDING = "待交接"
HANDOVER_DONE = "已完成"
HANDOVER_CANCELLED = "已撤销"

DATASET_ACTIVE = "生效中"
DATASET_ARCHIVED = "已归档"


class ScopeError(Exception):
    """权限/状态类业务错误，路由层按 status_code 转成 HTTP 响应。"""

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    team_id: str
    role: str
    team_name: str = ""

    @property
    def is_write(self) -> bool:
        return self.role == ROLE_WRITE


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class BatchScopeService:
    # ---------- 身份与基础数据 ----------
    def bootstrap(self) -> None:
        seed.bootstrap()

    def principal(self, team_id: str | None, role: str | None) -> Principal:
        self.bootstrap()
        if not team_id:
            raise ScopeError("缺少团队身份（X-Team-Id），爬虫引擎无法判定归属", 401)
        team = next((row for row in store.rows(TABLE_TEAMS) if row["team_id"] == team_id), None)
        if team is None:
            raise ScopeError(f"团队 {team_id} 未在爬虫引擎登记，拒绝访问", 403)
        normalized = (role or ROLE_READONLY).strip().lower()
        if normalized not in VALID_ROLES:
            raise ScopeError(f"角色 {role} 不合法，仅支持 write/readonly", 403)
        return Principal(team_id=team_id, role=normalized, team_name=str(team["team_name"]))

    def list_teams(self) -> list[dict[str, Any]]:
        self.bootstrap()
        return [dict(row) for row in store.rows(TABLE_TEAMS)]

    def _team_site_codes(self, team_id: str) -> set[str]:
        return {
            str(row["site_code"])
            for row in store.rows(TABLE_TEAM_SITES)
            if row["team_id"] == team_id
        }

    def _ownership_for_task(self, task_id: int) -> dict[str, Any]:
        entry = store.find(TABLE_OWNERSHIP, task_id)
        if entry is None:
            raise ScopeError(f"爬虫任务 {task_id} 不存在", 404)
        return entry

    def _require_write(self, principal: Principal, owner_team_id: str | None) -> None:
        if principal.role != ROLE_WRITE:
            raise ScopeError("只读账户不能改动归属受控数据，已被爬虫引擎拒绝", 403)
        if not owner_team_id:
            raise ScopeError("该爬虫任务没有归属授权团队，禁止任何团队启动新批次", 403)
        if principal.team_id != owner_team_id:
            raise ScopeError(
                f"越权访问：任务归属 {owner_team_id}，当前团队 {principal.team_id} 无写权限", 403)

    def _require_team_visibility(self, principal: Principal, owner_team_id: str | None) -> None:
        # 归属以爬虫引擎为准：非归属团队看不到别人的批次。
        if owner_team_id is None or principal.team_id != owner_team_id:
            raise ScopeError(
                f"越权访问：当前团队 {principal.team_id} 无权查看该归属批次", 403)

    # ---------- 任务归属与只读交接 ----------
    def list_ownership(self, principal: Principal) -> list[dict[str, Any]]:
        self.bootstrap()
        rows = store.rows(TABLE_OWNERSHIP)
        # 每个团队只看到归属自己（含交接中的自己一侧）以及无归属的登记行。
        visible = [
            dict(row) for row in rows
            if row.get("owner_team_id") == principal.team_id
            or row.get("write_team_id") == principal.team_id
            or row.get("status") == OWNERSHIP_NONE
        ]
        return visible

    def launch_permission(self, principal: Principal, task_id: int) -> dict[str, Any]:
        """复核归属切换前后的启动权限：原归属团队可看历史但不能启动新批次。"""
        self.bootstrap()
        entry = self._ownership_for_task(task_id)
        write_team = entry.get("write_team_id")
        can_launch = (
            principal.is_write
            and bool(write_team)
            and principal.team_id == write_team
            and entry.get("status") != OWNERSHIP_HANDOVER
        )
        reason = "允许启动新批次"
        if not principal.is_write:
            reason = "当前为只读账户，禁止启动新批次"
        elif not write_team:
            reason = "任务无归属授权团队，禁止启动新批次"
        elif principal.team_id != write_team:
            reason = f"当前团队不是写权限持有方（写权限：{write_team}），只能查看历史"
        elif entry.get("status") == OWNERSHIP_HANDOVER:
            reason = "任务归属交接中，写权限已冻结，禁止启动新批次"
        return {
            "crawler_task_id": task_id,
            "task_code": entry.get("task_code"),
            "owner_team_id": entry.get("owner_team_id"),
            "write_team_id": write_team,
            "ownership_status": entry.get("status"),
            "current_team_id": principal.team_id,
            "current_role": principal.role,
            "can_launch_new_batch": can_launch,
            "reason": reason,
        }

    def start_handover(
        self, principal: Principal, task_id: int, to_team_id: str, reason: str | None
    ) -> dict[str, Any]:
        self.bootstrap()
        with store.lock:
            entry = self._ownership_for_task(task_id)
            self._require_write(principal, entry.get("write_team_id"))
            if entry.get("status") == OWNERSHIP_NONE or not entry.get("owner_team_id"):
                raise ScopeError("无归属授权任务不能发起归属交接", 409)
            if entry.get("current_handover_id"):
                raise ScopeError("该任务已有进行中的归属交接，同一时刻只允许一个团队持有写权限", 409)
            target = next((r for r in store.rows(TABLE_TEAMS) if r["team_id"] == to_team_id), None)
            if target is None:
                raise ScopeError(f"接收团队 {to_team_id} 未登记", 404)
            if to_team_id == entry["owner_team_id"]:
                raise ScopeError("接收团队与当前归属团队相同，无需交接", 409)
            handover_rows = store.rows(TABLE_HANDOVERS)
            record = {
                "id": next_id(handover_rows),
                "handover_no": next_no(handover_rows, "handover_no", "HAND"),
                "crawler_task_id": task_id,
                "task_code": entry.get("task_code"),
                "from_team_id": entry["owner_team_id"],
                "to_team_id": to_team_id,
                "write_team_id": entry["owner_team_id"],
                "status": HANDOVER_PENDING,
                "reason": reason or "",
                "created_at": _now(),
                "completed_at": None,
            }
            handover_rows.append(record)
            # 交接期间写权限冻结：只有原团队持写、但状态置为交接中，新批次一律不允许启动。
            entry["status"] = OWNERSHIP_HANDOVER
            entry["current_handover_id"] = record["id"]
            return dict(record)

    def complete_handover(self, principal: Principal, handover_id: int) -> dict[str, Any]:
        """完成交接并裁决运行中批次的权限冲突。"""
        self.bootstrap()
        managed = [TABLE_OWNERSHIP, TABLE_HANDOVERS, TABLE_RUNS]
        with store.transaction(managed):
            handover = store.find(TABLE_HANDOVERS, handover_id)
            if handover is None:
                raise ScopeError(f"交接单 {handover_id} 不存在", 404)
            if handover["status"] != HANDOVER_PENDING:
                raise ScopeError(f"交接单当前状态为{handover['status']}，不能重复完成", 409)
            entry = self._ownership_for_task(int(handover["crawler_task_id"]))
            # 交接确认由当前写权限持有方（原归属团队）执行。
            self._require_write(principal, handover.get("write_team_id"))

            conflict_run_nos: list[str] = []
            authoritative_run: dict[str, Any] | None = None
            run_rows = store.rows(TABLE_RUNS)
            for run in run_rows:
                if int(run["crawler_task_id"]) != int(handover["crawler_task_id"]):
                    continue
                if run["status"] == RUN_RUNNING:
                    # 权限冲突：旧团队启动的批次跨越了归属边界，标记为被裁决替代，
                    # 它不会以“完整批次”身份进入下游数据集。
                    replacement_no = f"{run['run_no']}-R1"
                    run["status"] = RUN_DONE
                    run["conflict_resolved"] = True
                    run["superseded_by"] = replacement_no
                    run["handover_id"] = handover_id
                    run["finished_at"] = _now()
                    conflict_run_nos.append(str(run["run_no"]))

                    # 下游最终只取得一份权限边界明确的完整批次：
                    # 在新归属团队下生成权威替代批次，站点授权以新团队为准。
                    allowed_sites = self._team_site_codes(handover["to_team_id"])
                    site_code = run.get("site_code")
                    if site_code not in allowed_sites:
                        # 新团队无权访问该站点时，裁决批次不携带越权站点。
                        site_code = next(iter(allowed_sites), None)
                    site_name = ""
                    if site_code:
                        site_name = next(
                            (r["site_name"] for r in store.rows(TABLE_TEAM_SITES)
                             if r["team_id"] == handover["to_team_id"]
                             and r["site_code"] == site_code),
                            "",
                        )
                    authoritative_run = {
                        "id": next_id(run_rows),
                        "run_no": replacement_no,
                        "crawler_task_id": int(handover["crawler_task_id"]),
                        "task_code": handover["task_code"],
                        "owner_team_id": handover["to_team_id"],
                        "started_by_team_id": handover["to_team_id"],
                        "site_code": site_code,
                        "site_name": site_name,
                        "status": RUN_DONE,
                        "records": int(run.get("records") or 0),
                        "conflict_resolved": True,
                        "superseded_by": None,
                        "handover_id": handover_id,
                        "started_at": _now(),
                        "finished_at": _now(),
                    }
                    run_rows.append(authoritative_run)

            handover["status"] = HANDOVER_DONE
            handover["completed_at"] = _now()
            entry["owner_team_id"] = handover["to_team_id"]
            entry["write_team_id"] = handover["to_team_id"]
            entry["status"] = OWNERSHIP_ACTIVE
            entry["current_handover_id"] = None
            # 任务关联站点跟随新归属团队的授权边界，避免下游拿到越权站点。
            entry["site_codes"] = sorted(self._team_site_codes(handover["to_team_id"]))
            return {
                "handover": dict(handover),
                "conflict_run_nos": conflict_run_nos,
                "authoritative_run": dict(authoritative_run) if authoritative_run else None,
                "final_owner_team_id": handover["to_team_id"],
            }

    def cancel_handover(self, principal: Principal, handover_id: int) -> dict[str, Any]:
        self.bootstrap()
        with store.lock:
            handover = store.find(TABLE_HANDOVERS, handover_id)
            if handover is None:
                raise ScopeError(f"交接单 {handover_id} 不存在", 404)
            if handover["status"] != HANDOVER_PENDING:
                raise ScopeError("只有待交接的交接单可以撤销", 409)
            self._require_write(principal, handover.get("write_team_id"))
            handover["status"] = HANDOVER_CANCELLED
            handover["completed_at"] = _now()
            entry = self._ownership_for_task(int(handover["crawler_task_id"]))
            entry["status"] = OWNERSHIP_ACTIVE
            entry["current_handover_id"] = None
            return dict(handover)

    def list_handovers(self, principal: Principal, task_id: int | None = None) -> list[dict[str, Any]]:
        self.bootstrap()
        rows = store.rows(TABLE_HANDOVERS)
        items = [
            dict(row) for row in rows
            if row.get("from_team_id") == principal.team_id
            or row.get("to_team_id") == principal.team_id
            or row.get("write_team_id") == principal.team_id
        ]
        if task_id is not None:
            items = [row for row in items if int(row["crawler_task_id"]) == task_id]
        return items

    # ---------- 批次运行 ----------
    def start_run(self, principal: Principal, task_id: int) -> dict[str, Any]:
        self.bootstrap()
        with store.lock:
            entry = self._ownership_for_task(task_id)
            self._require_write(principal, entry.get("write_team_id"))
            if entry.get("status") == OWNERSHIP_HANDOVER:
                raise ScopeError("归属交接期间写权限冻结，原归属团队也不能启动新批次", 409)
            allowed_sites = self._team_site_codes(principal.team_id)
            task_sites = [code for code in entry.get("site_codes", []) if code in allowed_sites]
            if not task_sites:
                raise ScopeError("当前团队对该任务关联的站点均无访问授权，不能启动批次", 403)
            site_code = task_sites[0]
            site_name = next(
                (r["site_name"] for r in store.rows(TABLE_TEAM_SITES)
                 if r["team_id"] == principal.team_id and r["site_code"] == site_code),
                "",
            )
            run_rows = store.rows(TABLE_RUNS)
            run = {
                "id": next_id(run_rows),
                "run_no": next_no(run_rows, "run_no", "RUN"),
                "crawler_task_id": task_id,
                "task_code": entry.get("task_code"),
                "owner_team_id": principal.team_id,
                "started_by_team_id": principal.team_id,
                "site_code": site_code,
                "site_name": site_name,
                "status": RUN_RUNNING,
                "records": 0,
                "conflict_resolved": False,
                "superseded_by": None,
                "handover_id": None,
                "started_at": _now(),
                "finished_at": None,
            }
            run_rows.append(run)
            return dict(run)

    def finish_run(self, principal: Principal, run_id: int, records: int, failed: bool = False) -> dict[str, Any]:
        self.bootstrap()
        with store.lock:
            run = store.find(TABLE_RUNS, run_id)
            if run is None:
                raise ScopeError(f"批次 {run_id} 不存在", 404)
            self._require_write(principal, run.get("owner_team_id"))
            if run["status"] != RUN_RUNNING:
                raise ScopeError(f"批次当前状态为{run['status']}，不能重复收口", 409)
            run["status"] = RUN_FAILED if failed else RUN_DONE
            run["records"] = max(int(records), 0)
            run["finished_at"] = _now()
            return dict(run)

    def list_runs(self, principal: Principal, task_id: int | None = None) -> list[dict[str, Any]]:
        self.bootstrap()
        items: list[dict[str, Any]] = []
        for row in store.rows(TABLE_RUNS):
            owner = row.get("owner_team_id")
            # 无归属授权任务的运行记录：owner 为空，任何团队都看不到。
            if owner is None or owner != principal.team_id:
                continue
            if task_id is not None and int(row["crawler_task_id"]) != task_id:
                continue
            items.append(dict(row))
        return items

    # ---------- 归属受控数据集（事务 + 幂等 + 版本） ----------
    def create_dataset(
        self,
        principal: Principal,
        task_id: int,
        idempotency_key: str,
        *,
        force_snapshot_failure: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        if principal.role != ROLE_WRITE:
            raise ScopeError("只读账户不能生成归属受控数据集，已被爬虫引擎拒绝", 403)
        if not idempotency_key or not idempotency_key.strip():
            raise ScopeError("生成数据集必须提供幂等键", 400)

        with store.lock:
            entry = self._ownership_for_task(task_id)
            if not entry.get("owner_team_id"):
                raise ScopeError("该任务无归属授权，其运行记录禁止进入团队批次数据集", 403)
            if entry["owner_team_id"] != principal.team_id:
                raise ScopeError(
                    f"越权拒绝：任务归属 {entry['owner_team_id']}，数据集只能由归属团队生成", 403)

            # 幂等：同一团队 + 任务 + 幂等键的第二次提交只记一次，按成功回执返回。
            for existing in store.rows(TABLE_DATASETS):
                if (
                    existing["team_id"] == principal.team_id
                    and int(existing["crawler_task_id"]) == task_id
                    and existing["idempotency_key"] == idempotency_key
                ):
                    snapshot = store.find(TABLE_SNAPSHOTS, int(existing["snapshot_id"]))
                    return {
                        **dict(existing),
                        "deduplicated": True,
                        "snapshot": dict(snapshot) if snapshot else None,
                    }

            allowed_sites = self._team_site_codes(principal.team_id)
            candidate_runs: list[dict[str, Any]] = []
            excluded: list[dict[str, Any]] = []
            for run in store.rows(TABLE_RUNS):
                if int(run["crawler_task_id"]) != task_id:
                    continue
                reasons: list[str] = []
                if run.get("owner_team_id") is None:
                    reasons.append("无归属授权任务的运行记录")
                if run.get("owner_team_id") != principal.team_id:
                    reasons.append("非当前归属团队的越权批次")
                if run.get("superseded_by"):
                    reasons.append("权限冲突中已被权威批次替代")
                if run["status"] != RUN_DONE:
                    reasons.append(f"批次状态为{run['status']}，未形成完整批次")
                if run.get("site_code") not in allowed_sites:
                    reasons.append("站点不在当前团队授权范围内")
                if reasons:
                    excluded.append({"run_no": run["run_no"], "reasons": reasons})
                else:
                    candidate_runs.append(dict(run))

            if not candidate_runs:
                raise ScopeError(
                    "没有可归集的归属批次：无归属/越权/未完成批次均已排除，拒绝生成空数据集", 422)

            run_nos = [run["run_no"] for run in candidate_runs]
            version = 1 + sum(
                1 for row in store.rows(TABLE_DATASETS)
                if int(row["crawler_task_id"]) == task_id and row["team_id"] == principal.team_id
            )

            # 数据集与快照放进同一事务：快照失败时数据集一起回滚。
            with store.transaction(ALL_TABLES):
                dataset_rows = store.rows(TABLE_DATASETS)
                snapshot_rows = store.rows(TABLE_SNAPSHOTS)
                snapshot_id = next_id(snapshot_rows)
                snapshot_no = next_no(snapshot_rows, "snapshot_no", "SNAP")
                snapshot_sites = sorted({
                    str(run["site_code"])
                    for run in candidate_runs
                    if run.get("site_code") in allowed_sites
                })
                snapshot = {
                    "id": snapshot_id,
                    "snapshot_no": snapshot_no,
                    "team_id": principal.team_id,
                    "crawler_task_id": task_id,
                    "task_code": entry.get("task_code"),
                    "site_codes": snapshot_sites,
                    "site_count": len(snapshot_sites),
                    "run_nos": run_nos,
                    "source": "跨业务面采集目标",
                    "boundary_note": "仅包含当前团队有权访问的站点",
                    "created_at": _now(),
                }
                if force_snapshot_failure:
                    # 演示用：快照环节失败，事务必须把已写入的数据集一并回滚。
                    raise ScopeError("目标站点执行快照生成失败，数据集随事务回滚", 500)
                snapshot_rows.append(snapshot)

                receipt_no = next_no(dataset_rows, "receipt_no", "RCPT")
                # 新版本生成后，同任务的历史版本自动归档，保证同一时刻只有一个生效版本。
                for row in dataset_rows:
                    if (
                        int(row["crawler_task_id"]) == task_id
                        and row["team_id"] == principal.team_id
                    ):
                        row["status"] = DATASET_ARCHIVED
                dataset = {
                    "id": next_id(dataset_rows),
                    "dataset_no": next_no(dataset_rows, "dataset_no", "BSET"),
                    "team_id": principal.team_id,
                    "crawler_task_id": task_id,
                    "task_code": entry.get("task_code"),
                    "version": version,
                    "status": DATASET_ACTIVE,
                    "run_nos": run_nos,
                    "run_count": len(run_nos),
                    "excluded_runs": excluded,
                    "snapshot_id": snapshot_id,
                    "snapshot_no": snapshot_no,
                    "receipt_no": receipt_no,
                    "idempotency_key": idempotency_key,
                    "input_fields": list(DATASET_INPUT_FIELDS),
                    "output_fields": list(DATASET_OUTPUT_FIELDS),
                    "created_at": _now(),
                }
                dataset_rows.append(dataset)
                return {**dict(dataset), "deduplicated": False, "snapshot": dict(snapshot)}

    def list_datasets(self, principal: Principal, task_id: int | None = None) -> list[dict[str, Any]]:
        self.bootstrap()
        items = [
            dict(row) for row in store.rows(TABLE_DATASETS)
            if row["team_id"] == principal.team_id
        ]
        if task_id is not None:
            items = [row for row in items if int(row["crawler_task_id"]) == task_id]
        return items

    def get_dataset(self, principal: Principal, dataset_id: int) -> dict[str, Any]:
        self.bootstrap()
        dataset = store.find(TABLE_DATASETS, dataset_id)
        if dataset is None:
            raise ScopeError(f"数据集 {dataset_id} 不存在", 404)
        self._require_team_visibility(principal, dataset.get("team_id"))
        snapshot = store.find(TABLE_SNAPSHOTS, int(dataset["snapshot_id"]))
        return {**dict(dataset), "snapshot": dict(snapshot) if snapshot else None}

    def activate_dataset_version(self, principal: Principal, dataset_id: int) -> dict[str, Any]:
        """切换生效版本：同一任务只保留一个生效版本，且输入输出字段保持不变。"""
        self.bootstrap()
        if principal.role != ROLE_WRITE:
            raise ScopeError("只读账户不能切换数据集版本", 403)
        with store.lock:
            target = store.find(TABLE_DATASETS, dataset_id)
            if target is None:
                raise ScopeError(f"数据集 {dataset_id} 不存在", 404)
            if target["team_id"] != principal.team_id:
                raise ScopeError("越权拒绝：不能切换其他团队的数据集版本", 403)
            for row in store.rows(TABLE_DATASETS):
                if (
                    int(row["crawler_task_id"]) == int(target["crawler_task_id"])
                    and row["team_id"] == principal.team_id
                ):
                    row["status"] = DATASET_ARCHIVED
            target["status"] = DATASET_ACTIVE
            # 版本切换不改输入输出字段契约。
            target["input_fields"] = list(DATASET_INPUT_FIELDS)
            target["output_fields"] = list(DATASET_OUTPUT_FIELDS)
            return dict(target)

    # ---------- 目标站点执行快照（跨业务面落到采集目标清单） ----------
    def list_snapshots(self, principal: Principal, task_id: int | None = None) -> list[dict[str, Any]]:
        self.bootstrap()
        items = [
            dict(row) for row in store.rows(TABLE_SNAPSHOTS)
            if row["team_id"] == principal.team_id
        ]
        if task_id is not None:
            items = [row for row in items if int(row["crawler_task_id"]) == task_id]
        return items

    def target_snapshot_for_targetsite(self, principal: Principal) -> dict[str, Any]:
        """跨业务面：给采集目标模块返回当前团队权限边界内的目标站点执行快照清单。"""
        self.bootstrap()
        allowed = self._team_site_codes(principal.team_id)
        # 快照里只能包含当前团队有权访问的站点；即使历史快照混入越权站点也在出口过滤。
        sites = [
            dict(row) for row in store.rows(TABLE_TEAM_SITES)
            if row["team_id"] == principal.team_id and row["site_code"] in allowed
        ]
        snapshot_rows = [
            dict(row) for row in store.rows(TABLE_SNAPSHOTS)
            if row["team_id"] == principal.team_id
        ]
        for row in snapshot_rows:
            row["site_codes"] = [code for code in row.get("site_codes", []) if code in allowed]
            row["site_count"] = len(row["site_codes"])
        return {
            "team_id": principal.team_id,
            "team_name": principal.team_name,
            "authorized_sites": sites,
            "snapshot_manifest": snapshot_rows,
            "boundary_note": "快照只包含当前团队有权访问的站点，越权站点已在爬虫引擎出口剔除",
        }

    # ---------- 团队核对与审计 ----------
    def audit_report(self, principal: Principal) -> dict[str, Any]:
        """按团队核对任务归属、批次和只读交接记录，并复核归属切换前后的启动权限。"""
        self.bootstrap()
        ownership = self.list_ownership(principal)
        handovers = self.list_handovers(principal)
        runs = self.list_runs(principal)
        datasets = self.list_datasets(principal)
        snapshots = self.list_snapshots(principal)
        task_audit = []
        for entry in ownership:
            task_id = int(entry["id"])
            task_audit.append({
                "crawler_task_id": task_id,
                "task_code": entry.get("task_code"),
                "ownership_status": entry.get("status"),
                "owner_team_id": entry.get("owner_team_id"),
                "write_team_id": entry.get("write_team_id"),
                "launch_permission": self.launch_permission(principal, task_id),
                "runs": [dict(run) for run in runs if int(run["crawler_task_id"]) == task_id],
                "handovers": [dict(h) for h in handovers if int(h["crawler_task_id"]) == task_id],
            })
        return {
            "team_id": principal.team_id,
            "team_name": principal.team_name,
            "role": principal.role,
            "generated_at": _now(),
            "ownership_records": [dict(row) for row in ownership],
            "handover_records": handovers,
            "run_records": runs,
            "dataset_records": datasets,
            "snapshot_records": snapshots,
            "task_audit": task_audit,
        }
