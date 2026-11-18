"""血缘影响工作台接口：按操作批次发布去重断环后的影响传播结果，覆盖导出、复核与状态流转。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.impact import ImpactService

router = APIRouter(prefix="/api/impact", tags=["血缘影响工作台"])

service = ImpactService()

LIST_FIELDS = ["结果编号", "操作批次", "版本", "节点数", "边数", "环数量", "导出状态", "结果状态"]
STATUSES = ["待复核", "已发布", "已核对", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按操作批次或结果编号检索"),
    status: str | None = Query(default=None, description="待复核、已发布、已核对、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """待复核页列表：按操作批次过滤影响传播结果；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/build", response_model=ActionResult)
def build_result(payload: EntryPayload) -> ActionResult:
    """生成影响传播结果：同一操作批次只发布一份，重复生成返回存量版本，不会扩大结果。"""
    result, message = service.build_result(str(payload.values.get("操作批次") or ""))
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/result/{batch}", response_model=dict)
def get_result(batch: str) -> dict:
    """按操作批次查看去重后的节点、边和字段路径；重复查询不会无限展开。"""
    detail = service.detail(batch)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"操作批次 {batch} 尚未发布影响传播结果")
    return detail


@router.post("/result/{batch}/export", response_model=dict)
def export_result(batch: str, payload: EntryPayload) -> dict[str, Any]:
    """导出为血缘清单文件；上次导出失败或中断时，从已确认节点继续。"""
    limit_raw = str(payload.values.get("节点数上限") or "").strip()
    limit = int(limit_raw) if limit_raw else None
    manifest, message = service.export_result(batch, limit)
    if manifest is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "manifest": manifest}


@router.get("/result/{batch}/manifest")
def download_manifest(batch: str) -> JSONResponse:
    """下载字段路径：完整血缘清单文件，不影响导出进度。"""
    manifest = service.manifest(batch)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"操作批次 {batch} 尚未发布影响传播结果")
    return JSONResponse(
        content=manifest,
        headers={"Content-Disposition": f'attachment; filename="lineage-manifest-{batch}.json"'},
    )


@router.post("/import", response_model=ActionResult)
def import_review(payload: EntryPayload) -> ActionResult:
    """上传复核包：导入结果按操作批次保留来源版本，核对结果同步到血缘影响工作台。"""
    result, message, matched = service.import_review(payload.values)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=matched, message=message, entry=result)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单份影响传播结果执行发布结果、确认核对、归档结果；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
