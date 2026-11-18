"""数据回滚恢复业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.impact import ImpactService
from app.store import store

MODULE = "rollback"
REQUIRED_FIELDS = ["记录编号", "表名称", "回滚版本"]
STATUS_ORDER = ["待回滚", "回滚中", "已回滚", "已恢复"]
ACTION_RULES = {"执行回滚": "待回滚", "确认恢复": "回滚中", "归档记录": "已回滚"}
NEGATIVE_ACTIONS = []


class RollbackService:
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
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
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
            return None, f"回滚记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据回滚恢复可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"回滚记录已{action}"

    # ---------- 血缘影响联动 ----------
    def declare_compat(self, batch: str, version: Any) -> tuple[dict[str, Any] | None, str]:
        """回滚模块先声明血缘影响工作台中影响传播结果的兼容目标，再引用已有数据。"""
        return ImpactService().declare_compat(batch, "rollback", version)

    def attach_impact(self, batch: str) -> tuple[dict[str, Any] | None, str]:
        """回滚清单接入同一版本影响传播结果，保存受影响节点、边和无环路径。

        失败时只重试影响传播结果未完成项，回滚模块继续使用上一稳定口径。
        """
        batch = str(batch or "").strip()
        impact = ImpactService()
        result = impact.get_by_batch(batch)
        if result is None:
            return None, f"操作批次 {batch} 尚未发布影响传播结果，请先在血缘影响工作台生成"
        declared = impact.compat_for(batch, "rollback")
        if declared is None:
            return None, "回滚模块需先声明血缘影响工作台中影响传播结果的兼容目标，再引用已有数据"
        stable = result.get("稳定口径版本")
        stable_text = f"v{stable}" if stable else "（暂无）"
        if str(declared) != str(result["版本"]):
            return None, (
                f"影响传播结果当前版本 v{result['版本']} 与声明的兼容目标 v{declared} 不一致，"
                f"回滚模块继续使用上一稳定口径{stable_text}"
            )
        records = [row for row in store.rows(MODULE) if str(row.get("操作批次")) == batch]
        if not records:
            return None, f"操作批次 {batch} 没有需要接入的回滚记录"
        confirmed = set(result.get("已确认节点", []))
        incomplete: list[str] = []
        for record in records:
            table = str(record.get("表名称") or "").strip()
            names, sub_edges, sub_paths = impact.affected_from(result, table)
            detail = record.setdefault("影响同步明细", [])
            done = {item["表名"] for item in detail if item.get("状态") == "完成"}
            for name in names:
                if name in done:
                    continue  # 已完成项不重复处理，只重试未完成项
                status = "完成" if name in confirmed else "未完成"
                for item in detail:
                    if item["表名"] == name:
                        item["状态"] = status
                        break
                else:
                    detail.append({"表名": name, "状态": status})
            record["受影响节点"] = names
            record["受影响边"] = sub_edges
            record["无环路径"] = sub_paths
            pending_items = [item["表名"] for item in detail if item.get("状态") != "完成"]
            if pending_items:
                incomplete.extend(pending_items)
                record["影响传播结果版本"] = record.get("稳定口径版本")
            else:
                record["影响传播结果版本"] = result["版本"]
                record["稳定口径版本"] = result["版本"]
        if incomplete:
            todo = sorted(set(incomplete))
            return {"未完成项": todo}, (
                f"影响传播结果存在未完成项：{'、'.join(todo)}，已只重试未完成项，"
                f"回滚模块继续使用上一稳定口径{stable_text}"
            )
        impact.record_sync(batch, "rollback")
        return {"同步记录数": len(records)}, (
            f"回滚清单已接入 v{result['版本']} 影响传播结果，受影响节点、边与无环路径已保存，"
            "批次结果已同步至血缘影响工作台"
        )
