"""数据迁移管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.impact import ImpactService
from app.store import store

MODULE = "migrate"
REQUIRED_FIELDS = ["任务编号", "源表名", "目标表名"]
STATUS_ORDER = ["待执行", "迁移中", "已完成", "已回滚"]
ACTION_RULES = {"执行迁移": "待执行", "回滚迁移": "迁移中", "归档任务": "已完成"}
NEGATIVE_ACTIONS = ["回滚迁移"]


class MigrateService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"迁移任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据迁移管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"迁移任务已{action}"

    # ---------- 血缘影响联动 ----------
    def review_impact(self, batch: str) -> tuple[dict[str, Any] | None, str]:
        """读取影响传播结果，核对迁移映射是否被采纳，并把核对结果同步到血缘影响工作台。"""
        batch = str(batch or "").strip()
        impact = ImpactService()
        result = impact.get_by_batch(batch)
        if result is None:
            return None, f"操作批次 {batch} 尚未发布影响传播结果，无法核对"
        checks: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            field = str(row.get("映射字段") or "").strip()
            mapping = str(row.get("字段映射") or "").strip()
            if not field or not mapping:
                continue
            pairs = [p for p in mapping.replace("->", "→").split(";") if "→" in p]
            target_field = pairs[0].split("→", 1)[1].strip() if pairs else ""
            target_table = str(row.get("目标表名") or "").strip()
            reflected = any(
                f"{target_table}.{target_field}" in path
                for edge in result.get("边清单", [])
                for path in edge.get("字段路径", [])
            )
            conflict = next(
                (item for item in result.get("批次冲突", []) if item.get("字段") == field), None
            )
            if reflected:
                verdict = "已采纳"
            elif conflict is not None:
                verdict = f"未采纳（批次冲突采纳{conflict['采纳方向']}）"
            else:
                verdict = "未采纳"
            checks.append(
                {
                    "任务编号": row.get("任务编号"),
                    "映射字段": field,
                    "迁移映射": mapping,
                    "核对结论": verdict,
                }
            )
        if checks:
            conclusion = "通过" if all(item["核对结论"] == "已采纳" for item in checks) else "待确认"
        else:
            conclusion = "通过"
            checks.append({"核对结论": "通过", "说明": "该批次没有需要核对的迁移映射"})
        impact.record_review(batch, "migrate", conclusion, checks)
        return {"核对结论": conclusion, "明细": checks}, (
            f"迁移模块已读取影响传播结果，核对结论「{conclusion}」已同步到血缘影响工作台"
        )
