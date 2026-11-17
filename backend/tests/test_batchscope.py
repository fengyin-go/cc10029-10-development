"""归属受控爬虫批次数据集的后端验收测试。

覆盖需求控制点：
1. 按授权归属汇总采集批次，无归属授权任务的运行记录必须排除；
2. 越权批次不能进入团队可见的批次结果；
3. 归属变更期间只允许一个团队持有写权限，原团队可看历史但不能启动新批次；
4. 目标站点执行快照只包含当前团队有权访问的站点；
5. 运行中归属切换产生权限冲突时，下游只取得一份权限边界明确的完整批次；
6. 权限由爬虫引擎控制：越权拒绝、只读账户不能改动；
7. 数据集与快照同事务，数据集/快照任一失败一起回滚；
8. 同一请求第二次提交（幂等键）只记一次，按成功回执返回；
9. 版本切换保持既有输入输出字段；
10. 数据集状态落到跨业务面采集目标的目标站点执行快照清单；
11. 按团队核对任务归属、批次、只读交接记录，并复核归属切换前后的启动权限。
"""
from __future__ import annotations

import unittest

from starlette.testclient import TestClient

from app import batchscope_seed
from app.main import app
from app.store import store


class BatchScopeAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例把内存仓库恢复到初始种子，避免运行期数据互相污染。
        store.reset_to_seed()
        batchscope_seed._BOOTSTRAPPED = False
        self.client = TestClient(app)
        self.team_a_write = {"X-Team-Id": "TEAM-A", "X-Role": "write"}
        self.team_a_readonly = {"X-Team-Id": "TEAM-A", "X-Role": "readonly"}
        self.team_b_write = {"X-Team-Id": "TEAM-B", "X-Role": "write"}
        self.team_b_readonly = {"X-Team-Id": "TEAM-B", "X-Role": "readonly"}

    # 1. 无归属授权任务的运行记录被排除，团队只看到自己归属的批次
    def test_dataset_excludes_unowned_runs_and_aggregates_by_ownership(self) -> None:
        resp = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 1, "idempotency_key": "case-1"},
        )
        self.assertEqual(resp.status_code, 200)
        entry = resp.json()["entry"]
        # RUN-0006 属于无归属授权任务 4，绝不能出现在任何团队数据集里。
        self.assertNotIn("RUN-0006", entry["run_nos"])
        self.assertEqual(set(entry["run_nos"]), {"RUN-0001", "RUN-0002"})
        visible_runs = self.client.get("/api/batchscope/runs", headers=self.team_a_write).json()["items"]
        self.assertNotIn("RUN-0006", [run["run_no"] for run in visible_runs])
        # 失败批次 RUN-0005 未形成完整批次，同样被排除并给出原因。
        excluded = {item["run_no"]: item["reasons"] for item in entry["excluded_runs"]}
        self.assertIn("RUN-0005", excluded)

    def test_unowned_task_rejects_dataset_and_new_batch(self) -> None:
        resp = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 4, "idempotency_key": "x"},
        )
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/batchscope/tasks/4/runs", headers=self.team_a_write)
        self.assertEqual(resp.status_code, 403)

    # 2 & 6. 越权拒绝：非归属团队看不到也动不了别人的批次/数据集
    def test_cross_team_access_is_denied(self) -> None:
        resp = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_b_write,
            json={"crawler_task_id": 1, "idempotency_key": "cross"},
        )
        self.assertEqual(resp.status_code, 403)
        dataset = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 1, "idempotency_key": "owner"},
        ).json()["entry"]
        self.assertEqual(
            self.client.get(f"/api/batchscope/datasets/{dataset['id']}", headers=self.team_b_write).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/api/batchscope/datasets/{dataset['id']}/activate", headers=self.team_b_write).status_code,
            403,
        )
        # B 团队的批次清单里看不到 A 的批次。
        b_runs = [r["run_no"] for r in self.client.get("/api/batchscope/runs", headers=self.team_b_write).json()["items"]]
        self.assertTrue(set(b_runs).isdisjoint({"RUN-0001", "RUN-0002"}))

    # 6. 只读账户不能改动任何归属受控数据
    def test_readonly_account_cannot_modify(self) -> None:
        for method, path, payload in [
            ("POST", "/api/batchscope/tasks/1/runs", None),
            ("POST", "/api/batchscope/tasks/1/handovers", {"to_team_id": "TEAM-B"}),
            ("POST", "/api/batchscope/datasets", {"crawler_task_id": 1, "idempotency_key": "ro"}),
        ]:
            resp = self.client.request(method, path, headers=self.team_a_readonly, json=payload)
            self.assertEqual(resp.status_code, 403, path)
        # 只读账户可以读。
        self.assertEqual(self.client.get("/api/batchscope/runs", headers=self.team_a_readonly).status_code, 200)
        self.assertEqual(self.client.get("/api/batchscope/audit", headers=self.team_a_readonly).status_code, 200)
        # 缺少身份头一律拒绝。
        self.assertEqual(self.client.get("/api/batchscope/ownership").status_code, 401)

    # 3. 交接期间写权限冻结：原团队可看历史但不能启动新批次，且同时只有一个写团队
    def test_handover_freezes_write_but_keeps_history_visible(self) -> None:
        before = self.client.get("/api/batchscope/tasks/3/launch-permission", headers=self.team_a_write).json()
        self.assertTrue(before["can_launch_new_batch"])

        handover = self.client.post(
            "/api/batchscope/tasks/3/handovers",
            headers=self.team_a_write,
            json={"to_team_id": "TEAM-B", "reason": "职责调整"},
        ).json()["entry"]

        during = self.client.get("/api/batchscope/tasks/3/launch-permission", headers=self.team_a_write).json()
        self.assertFalse(during["can_launch_new_batch"])
        self.assertEqual(
            self.client.post("/api/batchscope/tasks/3/runs", headers=self.team_a_write).status_code, 409
        )
        # 历史批次仍可查看。
        history = self.client.get("/api/batchscope/runs?task_id=3", headers=self.team_a_write).json()["items"]
        self.assertTrue(any(run["run_no"] == "RUN-0004" for run in history))
        # 不能并行发起第二次交接。
        duplicate = self.client.post(
            "/api/batchscope/tasks/3/handovers",
            headers=self.team_a_write,
            json={"to_team_id": "TEAM-B"},
        )
        self.assertEqual(duplicate.status_code, 409)
        # 撤销后写权限恢复。
        self.client.post(f"/api/batchscope/handovers/{handover['id']}/cancel", headers=self.team_a_write)
        restored = self.client.get("/api/batchscope/tasks/3/launch-permission", headers=self.team_a_write).json()
        self.assertTrue(restored["can_launch_new_batch"])

    # 5. 运行中归属切换产生冲突：下游只拿到一份权限边界明确的权威完整批次
    def test_running_batch_conflict_arbitrates_to_single_authoritative_batch(self) -> None:
        handover = self.client.post(
            "/api/batchscope/tasks/3/handovers",
            headers=self.team_a_write,
            json={"to_team_id": "TEAM-B"},
        ).json()["entry"]
        result = self.client.post(
            f"/api/batchscope/handovers/{handover['id']}/complete", headers=self.team_a_write
        ).json()["entry"]

        self.assertEqual(result["conflict_run_nos"], ["RUN-0004"])
        authoritative = result["authoritative_run"]
        self.assertEqual(authoritative["run_no"], "RUN-0004-R1")
        self.assertEqual(authoritative["owner_team_id"], "TEAM-B")
        self.assertEqual(authoritative["status"], "已完成")
        # 权威批次的站点在 B 的授权边界内（B 只有 TARG-0003）。
        self.assertEqual(authoritative["site_code"], "TARG-0003")

        # 交接后 B 持写权限、A 失去写权限。
        self.assertTrue(
            self.client.get("/api/batchscope/tasks/3/launch-permission", headers=self.team_b_write).json()["can_launch_new_batch"]
        )
        self.assertFalse(
            self.client.get("/api/batchscope/tasks/3/launch-permission", headers=self.team_a_write).json()["can_launch_new_batch"]
        )

        # B 生成数据集：只含权威批次，被替代批次明确排除。
        dataset = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_b_write,
            json={"crawler_task_id": 3, "idempotency_key": "arb"},
        ).json()["entry"]
        self.assertEqual(dataset["run_nos"], ["RUN-0004-R1"])
        excluded_nos = [item["run_no"] for item in dataset["excluded_runs"]]
        self.assertIn("RUN-0004", excluded_nos)

    # 4 & 10. 快照只含授权站点，且数据集状态落到跨业务面采集目标快照清单
    def test_snapshot_contains_only_authorized_sites_and_lands_on_target_manifest(self) -> None:
        dataset = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 1, "idempotency_key": "snap"},
        ).json()["entry"]
        self.assertEqual(set(dataset["snapshot"]["site_codes"]), {"TARG-0001", "TARG-0002"})

        target_view = self.client.get(
            "/api/targetsite/execution-snapshot", headers=self.team_a_write
        ).json()
        site_codes = {site["site_code"] for site in target_view["authorized_sites"]}
        self.assertEqual(site_codes, {"TARG-0001", "TARG-0002"})
        manifest_nos = [item["snapshot_no"] for item in target_view["snapshot_manifest"]]
        self.assertIn(dataset["snapshot_no"], manifest_nos)

        # B 团队视角的跨业务面快照只包含 B 的站点。
        b_view = self.client.get("/api/targetsite/execution-snapshot", headers=self.team_b_write).json()
        self.assertEqual({s["site_code"] for s in b_view["authorized_sites"]}, {"TARG-0003"})

    # 7. 数据集与快照同事务：快照失败整体回滚，不留半成品
    def test_dataset_and_snapshot_rollback_together(self) -> None:
        before_datasets = len(self.client.get("/api/batchscope/datasets", headers=self.team_a_write).json()["items"])
        before_snapshots = len(self.client.get("/api/batchscope/snapshots", headers=self.team_a_write).json()["items"])
        resp = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={
                "crawler_task_id": 1,
                "idempotency_key": "fail",
                "force_snapshot_failure": True,
            },
        )
        self.assertEqual(resp.status_code, 500)
        after_datasets = len(self.client.get("/api/batchscope/datasets", headers=self.team_a_write).json()["items"])
        after_snapshots = len(self.client.get("/api/batchscope/snapshots", headers=self.team_a_write).json()["items"])
        self.assertEqual(before_datasets, after_datasets)
        self.assertEqual(before_snapshots, after_snapshots)

    # 8. 幂等：第二次提交只记一次，按成功回执返回同一条记录
    def test_duplicate_submission_is_idempotent_with_single_receipt(self) -> None:
        payload = {"crawler_task_id": 1, "idempotency_key": "idem-key"}
        first = self.client.post("/api/batchscope/datasets", headers=self.team_a_write, json=payload).json()["entry"]
        second = self.client.post("/api/batchscope/datasets", headers=self.team_a_write, json=payload).json()["entry"]
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["receipt_no"], second["receipt_no"])
        items = self.client.get("/api/batchscope/datasets?task_id=1", headers=self.team_a_write).json()["items"]
        self.assertEqual(len(items), 1)

    # 9. 版本切换保持输入输出字段，且同一任务只有一个生效版本
    def test_version_switch_keeps_contract_fields(self) -> None:
        first = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 1, "idempotency_key": "v1"},
        ).json()["entry"]
        second = self.client.post(
            "/api/batchscope/datasets",
            headers=self.team_a_write,
            json={"crawler_task_id": 1, "idempotency_key": "v2"},
        ).json()["entry"]
        self.assertEqual(first["input_fields"], second["input_fields"])
        self.assertEqual(first["output_fields"], second["output_fields"])
        # 新版本生成后旧版本自动归档。
        stored = {d["id"]: d for d in self.client.get(
            "/api/batchscope/datasets?task_id=1", headers=self.team_a_write).json()["items"]}
        self.assertEqual(stored[first["id"]]["status"], "已归档")
        self.assertEqual(stored[second["id"]]["status"], "生效中")
        # 手动切回旧版本，字段契约不变。
        switched = self.client.post(
            f"/api/batchscope/datasets/{first['id']}/activate", headers=self.team_a_write
        ).json()["entry"]
        self.assertEqual(switched["status"], "生效中")
        self.assertEqual(switched["input_fields"], first["input_fields"])
        self.assertEqual(switched["output_fields"], first["output_fields"])
        self.assertEqual(
            self.client.get(
                f"/api/batchscope/datasets/{second['id']}", headers=self.team_a_write
            ).json()["status"],
            "已归档",
        )

    # 11. 团队审计：归属、批次、只读交接记录与切换前后启动权限可核对
    def test_audit_report_covers_ownership_runs_handovers_and_launch_permission(self) -> None:
        handover = self.client.post(
            "/api/batchscope/tasks/3/handovers",
            headers=self.team_a_write,
            json={"to_team_id": "TEAM-B"},
        ).json()["entry"]
        self.client.post(f"/api/batchscope/handovers/{handover['id']}/complete", headers=self.team_a_write)

        audit_b = self.client.get("/api/batchscope/audit", headers=self.team_b_readonly).json()
        self.assertEqual(audit_b["team_id"], "TEAM-B")
        self.assertIn("ownership_records", audit_b)
        self.assertIn("handover_records", audit_b)
        self.assertIn("run_records", audit_b)
        task3 = next(item for item in audit_b["task_audit"] if item["crawler_task_id"] == 3)
        # 只读身份下审计同样如实反映“只读账户不能启动新批次”。
        self.assertFalse(task3["launch_permission"]["can_launch_new_batch"])
        self.assertTrue(any(h["status"] == "已完成" for h in task3["handovers"]))
        # 交接后任务的写权限归属 B；用写身份复核可以启动。
        self.assertEqual(task3["write_team_id"], "TEAM-B")
        self.assertEqual(task3["owner_team_id"], "TEAM-B")
        self.assertTrue(
            self.client.get(
                "/api/batchscope/tasks/3/launch-permission", headers=self.team_b_write
            ).json()["can_launch_new_batch"]
        )

        audit_a = self.client.get("/api/batchscope/audit", headers=self.team_a_readonly).json()
        # 交接后任务 3 不再出现在 A 的归属清单（A 已失去写权限），
        # 但只读交接留痕仍在 A 的交接记录区可供核对。
        self.assertTrue(
            all(item["crawler_task_id"] != 3 for item in audit_a["task_audit"])
        )
        self.assertTrue(
            any(h["handover_no"] == handover["handover_no"] for h in audit_a["handover_records"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
