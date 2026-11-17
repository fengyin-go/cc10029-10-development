"""归属受控爬虫批次数据集接口。

身份通过请求头传入：
- X-Team-Id：当前操作团队（TEAM-A / TEAM-B）
- X-Role：write（读写）或 readonly（只读，默认）

权限判断全部下沉到 BatchScopeService，路由层只负责把 ScopeError 转成 HTTP 状态码。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.services.batchscope import BatchScopeService, ScopeError

router = APIRouter(prefix="/api/batchscope", tags=["归属受控批次"])
service = BatchScopeService()


class HandoverPayload(BaseModel):
    to_team_id: str
    reason: str | None = None


class RunFinishPayload(BaseModel):
    records: int = 0
    failed: bool = False


class DatasetPayload(BaseModel):
    crawler_task_id: int
    idempotency_key: str = Field(min_length=1)
    force_snapshot_failure: bool = False


def _principal(x_team_id: str | None, x_role: str | None):
    try:
        return service.principal(x_team_id, x_role)
    except ScopeError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


def _call(action, *args, **kwargs) -> Any:
    try:
        return action(*args, **kwargs)
    except ScopeError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


# ---------- 团队与归属 ----------
@router.get("/teams")
def list_teams() -> dict[str, Any]:
    return {"items": service.list_teams()}


@router.get("/ownership")
def list_ownership(
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"items": _call(service.list_ownership, principal)}


@router.get("/tasks/{task_id}/launch-permission")
def launch_permission(
    task_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return _call(service.launch_permission, principal, task_id)


# ---------- 归属交接（只读交接记录） ----------
@router.post("/tasks/{task_id}/handovers")
def start_handover(
    task_id: int,
    payload: HandoverPayload,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.start_handover, principal, task_id, payload.to_team_id, payload.reason)}


@router.post("/handovers/{handover_id}/complete")
def complete_handover(
    handover_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.complete_handover, principal, handover_id)}


@router.post("/handovers/{handover_id}/cancel")
def cancel_handover(
    handover_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.cancel_handover, principal, handover_id)}


@router.get("/handovers")
def list_handovers(
    task_id: int | None = None,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"items": _call(service.list_handovers, principal, task_id)}


# ---------- 批次运行 ----------
@router.post("/tasks/{task_id}/runs")
def start_run(
    task_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.start_run, principal, task_id)}


@router.post("/runs/{run_id}/finish")
def finish_run(
    run_id: int,
    payload: RunFinishPayload,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.finish_run, principal, run_id, payload.records, payload.failed)}


@router.get("/runs")
def list_runs(
    task_id: int | None = None,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"items": _call(service.list_runs, principal, task_id)}


# ---------- 归属受控数据集 ----------
@router.post("/datasets")
def create_dataset(
    payload: DatasetPayload,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    entry = _call(
        service.create_dataset,
        principal,
        payload.crawler_task_id,
        payload.idempotency_key,
        force_snapshot_failure=payload.force_snapshot_failure,
    )
    return {"ok": True, "message": "归属受控爬虫批次数据集已生成（含同事务站点快照）", "entry": entry}


@router.get("/datasets")
def list_datasets(
    task_id: int | None = None,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"items": _call(service.list_datasets, principal, task_id)}


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return _call(service.get_dataset, principal, dataset_id)


@router.post("/datasets/{dataset_id}/activate")
def activate_dataset(
    dataset_id: int,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"ok": True, "entry": _call(service.activate_dataset_version, principal, dataset_id)}


# ---------- 目标站点执行快照 ----------
@router.get("/snapshots")
def list_snapshots(
    task_id: int | None = None,
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return {"items": _call(service.list_snapshots, principal, task_id)}


@router.get("/target-snapshot")
def target_snapshot(
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return _call(service.target_snapshot_for_targetsite, principal)


# ---------- 审计核对 ----------
@router.get("/audit")
def audit_report(
    x_team_id: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict[str, Any]:
    principal = _principal(x_team_id, x_role)
    return _call(service.audit_report, principal)
