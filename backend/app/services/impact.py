"""血缘影响工作台业务规则：影响传播结果的去重、断环、发布、导出与复核都收在这里。

核心口径：
- 同一操作批次只发布一份影响传播结果，重复生成/查询不会扩大结果；
- 沿实际数据流展开时用访问标记断环，环不重复展开，节点与边都带字段路径；
- 同一字段的迁移映射与回滚映射批次冲突时只采纳一条方向，待复核页只看到一份无环结果；
- 导出失败或中断后从已确认节点继续；导入复核包按操作批次保留来源版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

RESULT_MODULE = "impact"
COMPAT_MODULE = "impact_compat"
LINEAGE_MODULE = "lineage"
ACTIVE_STATUSES = {"已记录", "已关联"}
STATUS_ORDER = ["待复核", "已发布", "已核对", "已归档"]
ACTION_RULES = {"发布结果": "已发布", "确认核对": "已核对", "归档结果": "已归档"}
NEGATIVE_ACTIONS: list[str] = []
MAX_PATHS = 100
MAX_NODE_PATHS = 20


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_mappings(raw: Any) -> list[tuple[str, str]]:
    """把「上游字段→下游字段;…」形式的字段映射解析成有序去重的字段对。"""
    text = str(raw or "").replace("->", "→")
    pairs: list[tuple[str, str]] = []
    for chunk in text.replace("；", ";").replace("，", ";").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if "→" not in chunk:
            continue
        left, right = chunk.split("→", 1)
        left, right = left.strip(), right.strip()
        if left and right and (left, right) not in pairs:
            pairs.append((left, right))
    return pairs


class ImpactService:
    # ---------- 待复核页查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(RESULT_MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("操作批次", "")) or keyword in str(row.get("结果编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(RESULT_MODULE, entry_id)

    def get_by_batch(self, batch: str) -> dict[str, Any] | None:
        for row in store.rows(RESULT_MODULE):
            if str(row.get("操作批次")) == str(batch):
                return row
        return None

    def versions_by_batch(self) -> dict[str, int]:
        """血缘列表与影响视图共用这一份版本口径，保证两处指向同版本。"""
        return {str(row.get("操作批次")): int(row.get("版本", 0)) for row in store.rows(RESULT_MODULE)}

    def detail(self, batch: str) -> dict[str, Any] | None:
        result = self.get_by_batch(batch)
        if result is None:
            return None
        payload = dict(result)
        payload["兼容目标"] = self.compat_list(batch)
        return payload

    # ---------- 生成：去重 + 断环 + 冲突消解 ----------
    def build_result(self, batch: str) -> tuple[dict[str, Any] | None, str]:
        batch = str(batch or "").strip()
        if not batch:
            return None, "请先填写操作批次"
        existing = self.get_by_batch(batch)
        if existing is not None:
            return existing, (
                f"操作批次 {batch} 已发布 v{existing['版本']}，同一批次只保留一份影响结果，"
                "重复生成不会扩大结果"
            )
        records = [row for row in store.rows(LINEAGE_MODULE) if row.get("status") in ACTIVE_STATUSES]
        batch_records = [row for row in records if str(row.get("操作批次")) == batch]
        if not batch_records:
            return None, f"操作批次 {batch} 没有可用的血缘记录，无法生成影响传播结果"

        # 1) 血缘记录去重：同一对上下游表只保留一条边，字段映射、来源记录与来源批次合并
        edge_map: dict[tuple[str, str], dict[str, Any]] = {}
        edge_order: list[tuple[str, str]] = []
        for row in records:
            upstream = str(row.get("上游表名") or "").strip()
            downstream = str(row.get("下游表名") or "").strip()
            if not upstream or not downstream:
                continue
            key = (upstream, downstream)
            if key not in edge_map:
                edge_map[key] = {"字段映射": [], "来源记录": [], "来源批次": []}
                edge_order.append(key)
            item = edge_map[key]
            for pair in _parse_mappings(row.get("字段映射")):
                if pair not in item["字段映射"]:
                    item["字段映射"].append(pair)
            for coll, val in (("来源记录", row.get("记录编号")), ("来源批次", row.get("操作批次"))):
                if val and val not in item[coll]:
                    item[coll].append(val)

        adjacency: dict[str, list[tuple[str, str]]] = {}
        for key in edge_order:
            adjacency.setdefault(key[0], []).append(key)

        # 2) 批次冲突：同一字段的迁移映射与回滚映射只采纳一条方向
        conflicts, direction = self._resolve_conflicts(batch)

        # 冲突消解后的有效字段映射（边与节点的字段路径共用同一口径）
        effective_pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for key in edge_order:
            pairs: list[tuple[str, str]] = []
            for pair in edge_map[key]["字段映射"]:
                override = direction.get(f"{key[0]}.{pair[0]}")
                parsed = _parse_mappings(override) if override else []
                pairs.append(parsed[0] if parsed else pair)
            effective_pairs[key] = pairs

        # 3) 沿实际数据流展开：访问标记断环，环不重复展开
        seeds: list[str] = []
        for row in batch_records:
            for table in (str(row.get("上游表名") or "").strip(), str(row.get("下游表名") or "").strip()):
                if table and table not in seeds:
                    seeds.append(table)

        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str], dict[str, Any]] = {}
        cut_edges: dict[tuple[str, str], dict[str, Any]] = {}
        discovery: list[tuple[str, str]] = []
        visited: set[str] = set()

        def ensure_node(name: str, depth: int) -> None:
            node = nodes.get(name)
            if node is None:
                nodes[name] = {"表名": name, "深度": depth, "字段路径": [], "来源记录": []}
            else:
                node["深度"] = min(int(node["深度"]), depth)

        def dfs(upstream: str, depth: int, path: frozenset[str]) -> None:
            visited.add(upstream)
            ensure_node(upstream, depth)
            for key in adjacency.get(upstream, []):
                downstream = key[1]
                info = edge_map[key]
                if downstream in path:
                    cut_edges[key] = {
                        "上游表名": key[0],
                        "下游表名": downstream,
                        "来源记录": list(info["来源记录"]),
                        "原因": "目标节点已在当前展开路径中，断环去重",
                    }
                    continue
                if key not in edges:
                    edges[key] = {
                        "上游表名": key[0],
                        "下游表名": downstream,
                        "字段路径": [
                            f"{key[0]}.{left}→{downstream}.{right}"
                            for left, right in effective_pairs[key]
                        ],
                        "来源记录": list(info["来源记录"]),
                        "来源批次": sorted(str(item) for item in info["来源批次"]),
                    }
                    discovery.append(key)
                if downstream not in visited:
                    dfs(downstream, depth + 1, path | {downstream})

        for seed in seeds:
            if seed not in visited:
                dfs(seed, 0, frozenset({seed}))

        # 节点来源记录：保留边两端贡献的血缘记录编号
        for key, info in edge_map.items():
            for name in key:
                if name not in nodes:
                    continue
                for code in info["来源记录"]:
                    if code not in nodes[name]["来源记录"]:
                        nodes[name]["来源记录"].append(code)

        # 4) 节点字段路径：从根节点沿去重边逐列拼接
        incoming = {key[1] for key in edges}
        roots = [name for name in nodes if name not in incoming]
        node_paths: dict[str, list[str]] = {name: [] for name in nodes}
        for root in roots:
            columns: list[str] = []
            for key in discovery:
                if key[0] != root:
                    continue
                for left, _ in effective_pairs[key]:
                    if left not in columns:
                        columns.append(left)
            node_paths[root] = [f"{root}.{col}" for col in columns] or [root]
        for _ in range(2):  # 交叉边场景补一轮，保证上游前缀已就绪
            for key in discovery:
                upstream, downstream = key
                for left, right in effective_pairs[key]:
                    prefixes = (
                        [p for p in node_paths[upstream] if p.rsplit(".", 1)[-1] == left]
                        or node_paths[upstream]
                        or [upstream]
                    )
                    for prefix in prefixes[:5]:
                        text = f"{prefix}→{downstream}.{right}"
                        if text not in node_paths[downstream] and len(node_paths[downstream]) < MAX_NODE_PATHS:
                            node_paths[downstream].append(text)
        for name, paths in node_paths.items():
            nodes[name]["字段路径"] = paths

        # 5) 无环路径：沿去重后的边枚举根到叶的路径，供回滚清单保存
        downstream_map: dict[str, list[str]] = {}
        for key in discovery:
            downstream_map.setdefault(key[0], []).append(key[1])
        acyclic: list[list[str]] = []

        def walk(node: str, path: list[str]) -> None:
            if len(acyclic) >= MAX_PATHS:
                return
            next_hops = [item for item in downstream_map.get(node, []) if item not in path]
            if not next_hops:
                acyclic.append(list(path))
                return
            for nxt in next_hops:
                walk(nxt, path + [nxt])

        for root in roots:
            if len(acyclic) >= MAX_PATHS:
                break
            walk(root, [root])

        # 6) 异常定位：沿实际数据流找出命中结果的异常血缘记录
        abnormal_sources = sorted(
            {
                str(row.get("记录编号"))
                for row in records
                if row.get("abnormal")
                and (str(row.get("上游表名")), str(row.get("下游表名"))) in {
                    **{key: True for key in edges},
                    **{key: True for key in cut_edges},
                }
            }
        )

        ordered_nodes = sorted(nodes.values(), key=lambda n: (int(n["深度"]), str(n["表名"])))
        rows = store.rows(RESULT_MODULE)
        result: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "结果编号": f"IMP-{batch}",
            "操作批次": batch,
            "版本": 1,
            "status": STATUS_ORDER[0],
            "结果状态": STATUS_ORDER[0],
            "pending": True,
            "abnormal": bool(abnormal_sources),
            "异常来源": abnormal_sources,
            "节点清单": ordered_nodes,
            "边清单": [edges[key] for key in discovery],
            "无环路径": acyclic,
            "断环边": list(cut_edges.values()),
            "环数量": len(cut_edges),
            "批次冲突": conflicts,
            "节点数": len(ordered_nodes),
            "边数": len(edges),
            "已确认节点": [],
            "导出状态": "未导出",
            "核对记录": [],
            "同步记录": [],
            "稳定口径版本": None,
            "生成时间": _now(),
        }
        rows.append(result)
        return result, (
            f"影响传播结果已生成：去重后 {len(ordered_nodes)} 个节点、{len(edges)} 条边，"
            f"断环 {len(cut_edges)} 处"
        )

    def _resolve_conflicts(self, batch: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """同一字段的迁移映射与回滚映射批次冲突时，只采纳一条方向。

        回滚模块已声明兼容目标时沿回滚保留的旧链路展开，否则按较新批次沿迁移后的新链路展开。
        """
        migrate_maps = self._mapping_rows("migrate")
        rollback_maps = self._mapping_rows("rollback")
        conflicts: list[dict[str, Any]] = []
        direction: dict[str, str] = {}
        declared = self.compat_for(batch, "rollback")
        for field in sorted(set(migrate_maps) & set(rollback_maps)):
            migrate_batch, migrate_map = migrate_maps[field]
            rollback_batch, rollback_map = rollback_maps[field]
            if migrate_batch == rollback_batch and migrate_map == rollback_map:
                continue
            if declared is not None:
                way, adopted = "回滚旧链路", rollback_map
            elif str(migrate_batch) >= str(rollback_batch):
                way, adopted = "迁移新链路", migrate_map
            else:
                way, adopted = "回滚旧链路", rollback_map
            direction[field] = adopted
            conflicts.append(
                {
                    "字段": field,
                    "迁移批次": migrate_batch,
                    "回滚批次": rollback_batch,
                    "采纳方向": way,
                    "采纳映射": adopted,
                }
            )
        return conflicts, direction

    @staticmethod
    def _mapping_rows(module: str) -> dict[str, tuple[str, str]]:
        maps: dict[str, tuple[str, str]] = {}
        for row in store.rows(module):
            field = str(row.get("映射字段") or "").strip()
            mapping = str(row.get("字段映射") or "").strip()
            if field and mapping:
                maps[field] = (str(row.get("操作批次") or ""), mapping)
        return maps

    # ---------- 状态流转 ----------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(RESULT_MODULE, entry_id)
        if entry is None:
            return None, f"影响传播结果 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于血缘影响工作台可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        self._set_status(entry, target)
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"影响传播结果已{action}"

    @staticmethod
    def _set_status(result: dict[str, Any], status: str) -> None:
        if result.get("status") == STATUS_ORDER[-1]:
            return  # 已归档不再流转
        result["status"] = status
        result["结果状态"] = status
        result["pending"] = status in STATUS_ORDER[:2]

    # ---------- 导出：血缘清单文件，中断后从已确认节点继续 ----------
    def export_result(self, batch: str, limit: int | None) -> tuple[dict[str, Any] | None, str]:
        result = self.get_by_batch(batch)
        if result is None:
            return None, f"操作批次 {batch} 尚未发布影响传播结果"
        confirmed: list[str] = result.setdefault("已确认节点", [])
        remaining = [node for node in result["节点清单"] if node["表名"] not in confirmed]
        if not remaining:
            result["导出状态"] = "已完成"
            return self._manifest_chunk(result, []), "血缘清单已全部导出，没有待确认节点"
        chunk = remaining if limit is None else remaining[: max(int(limit), 0)]
        if not chunk:
            return None, "本次导出节点数不能为 0"
        for node in chunk:
            confirmed.append(node["表名"])
        left = [node for node in result["节点清单"] if node["表名"] not in confirmed]
        result["导出状态"] = "已完成" if not left else "已中断"
        if left:
            message = (
                f"导出中断，已确认 {len(confirmed)} 个节点，续传起点 {left[0]['表名']}，"
                "再次导出将从已确认节点继续"
            )
        else:
            message = "血缘清单导出完成"
        return self._manifest_chunk(result, chunk), message

    def _manifest_chunk(self, result: dict[str, Any], chunk: list[dict[str, Any]]) -> dict[str, Any]:
        confirmed = set(result.get("已确认节点", []))
        return {
            "清单类型": "血缘清单文件",
            "结果编号": result["结果编号"],
            "操作批次": result["操作批次"],
            "版本": result["版本"],
            "续传起点": chunk[0]["表名"] if chunk else None,
            "本次导出节点": chunk,
            "本次导出边": [
                edge
                for edge in result["边清单"]
                if edge["上游表名"] in confirmed and edge["下游表名"] in confirmed
            ],
            "已确认节点": list(result.get("已确认节点", [])),
            "待确认节点": [node["表名"] for node in result["节点清单"] if node["表名"] not in confirmed],
            "导出状态": result["导出状态"],
            "导出时间": _now(),
        }

    def manifest(self, batch: str) -> dict[str, Any] | None:
        """下载字段路径：完整血缘清单文件，不改变导出进度。"""
        result = self.get_by_batch(batch)
        if result is None:
            return None
        return {
            "清单类型": "血缘清单文件",
            "结果编号": result["结果编号"],
            "操作批次": result["操作批次"],
            "版本": result["版本"],
            "结果状态": result["status"],
            "节点清单": result["节点清单"],
            "边清单": result["边清单"],
            "无环路径": result["无环路径"],
            "断环边": result["断环边"],
            "环数量": result["环数量"],
            "批次冲突": result["批次冲突"],
            "导出时间": _now(),
        }

    # ---------- 复核：上传复核包，按操作批次保留来源版本 ----------
    def import_review(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        batch = str(values.get("操作批次") or "").strip()
        result = self.get_by_batch(batch)
        if result is None:
            return None, f"操作批次 {batch or '（空）'} 尚未发布影响传播结果，无法导入复核包", False
        source_version = str(values.get("来源版本") or "").strip().lstrip("vV")
        module = str(values.get("核对模块") or "复核包").strip() or "复核包"
        conclusion = str(values.get("核对结果") or "").strip() or "待确认"
        matched = source_version == str(result["版本"])
        result.setdefault("核对记录", []).append(
            {
                "来源模块": module,
                "来源版本": source_version,
                "核对结果": conclusion if matched else "版本不一致",
                "匹配当前版本": matched,
                "核对时间": _now(),
            }
        )
        if not matched:
            return result, (
                f"来源版本 v{source_version or '?'} 与当前发布版本 v{result['版本']} 不一致，"
                "已按操作批次保留来源版本记录"
            ), False
        if conclusion == "通过":
            self._set_status(result, "已核对")
            result["稳定口径版本"] = result["版本"]
        return result, "复核包已导入，核对结果已同步到血缘影响工作台", True

    def record_review(
        self, batch: str, module: str, conclusion: str, detail: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """其他处理模块读取影响传播结果后，把核对结果同步回血缘影响工作台。"""
        result = self.get_by_batch(batch)
        if result is None:
            return None
        result.setdefault("核对记录", []).append(
            {
                "来源模块": module,
                "来源版本": str(result["版本"]),
                "核对结果": conclusion,
                "匹配当前版本": True,
                "明细": detail,
                "核对时间": _now(),
            }
        )
        if conclusion == "通过":
            self._set_status(result, "已核对")
            result["稳定口径版本"] = result["版本"]
        return result

    def record_sync(self, batch: str, module: str) -> dict[str, Any] | None:
        """批次结果同步至血缘影响工作台，并把当前版本记为稳定口径。"""
        result = self.get_by_batch(batch)
        if result is None:
            return None
        result.setdefault("同步记录", []).append(
            {"来源模块": module, "版本": result["版本"], "同步时间": _now()}
        )
        result["稳定口径版本"] = result["版本"]
        return result

    # ---------- 兼容目标：回滚模块先声明，再引用已有数据 ----------
    def declare_compat(self, batch: str, module: str, version: Any) -> tuple[dict[str, Any] | None, str]:
        batch = str(batch or "").strip()
        version_text = str(version or "").strip().lstrip("vV")
        if not batch or not version_text:
            return None, "请先填写操作批次与兼容版本"
        rows = store.rows(COMPAT_MODULE)
        for row in rows:
            if str(row.get("操作批次")) == batch and row.get("模块") == module:
                row["兼容版本"] = version_text
                row["声明时间"] = _now()
                declaration = row
                break
        else:
            declaration = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "操作批次": batch,
                "模块": module,
                "兼容版本": version_text,
                "声明时间": _now(),
                "pending": False,
                "abnormal": False,
            }
            rows.append(declaration)
        result = self.get_by_batch(batch)
        if result is None:
            return declaration, f"已向血缘影响工作台声明兼容目标 v{version_text}，影响传播结果尚未发布，将在生成时生效"
        return declaration, f"已向血缘影响工作台声明兼容目标 v{version_text}（当前发布 v{result['版本']}）"

    def compat_for(self, batch: str, module: str) -> str | None:
        for row in store.rows(COMPAT_MODULE):
            if str(row.get("操作批次")) == str(batch) and row.get("模块") == module:
                return str(row.get("兼容版本"))
        return None

    def compat_list(self, batch: str) -> list[dict[str, Any]]:
        return [row for row in store.rows(COMPAT_MODULE) if str(row.get("操作批次")) == str(batch)]

    # ---------- 回滚清单取数：按表名取受影响节点、边与无环路径 ----------
    @staticmethod
    def affected_from(
        result: dict[str, Any], table: str
    ) -> tuple[list[str], list[dict[str, Any]], list[list[str]]]:
        edges = result.get("边清单", [])
        downstream_map: dict[str, list[str]] = {}
        for edge in edges:
            downstream_map.setdefault(edge["上游表名"], []).append(edge["下游表名"])
        seen: set[str] = set()
        stack = [table]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(downstream_map.get(current, []))
        names = [node["表名"] for node in result.get("节点清单", []) if node["表名"] in seen]
        sub_edges = [edge for edge in edges if edge["上游表名"] in seen and edge["下游表名"] in seen]
        sub_paths = [path for path in result.get("无环路径", []) if table in path]
        return names, sub_edges, sub_paths
