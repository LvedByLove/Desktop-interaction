# -*- coding: utf-8 -*-
"""
低成本内存采样诊断器（默认关闭，仅调试用）。

启用方式（环境变量）：
    XIAOZHI_MEM_PROFILE=1          # 开启采样（总开关）
    XIAOZHI_MEM_INTERVAL=120       # 采样间隔（秒），默认 60
    XIAOZHI_MEM_TOP=15             # 每次输出增长最多的前 N 个分配点，默认 15
    XIAOZHI_MEM_TRACE=1            # 可选：额外开启 tracemalloc（分配点追踪）

说明：
- 默认只做低成本采样：RSS、GC 对象数、活动任务、垃圾对象、句柄、线程数。
  开销极小，可长期挂机观察内存趋势，不影响日常使用。
- 仅在显式设置 XIAOZHI_MEM_TRACE=1 时才启用 tracemalloc。tracemalloc 为
  每个活跃对象保留调用栈，1.25M 对象会使记录开销达到数百 MB，会污染 RSS
  读数并造成明显卡顿，只能短时用于定位具体漏点。
"""

import asyncio
import collections
import gc
import logging
import os

logger = logging.getLogger("memory_profiler")

_ENV_ENABLE = "XIAOZHI_MEM_PROFILE"
_ENV_INTERVAL = "XIAOZHI_MEM_INTERVAL"
_ENV_TOP = "XIAOZHI_MEM_TOP"
_ENV_TRACE = "XIAOZHI_MEM_TRACE"


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    return _env_bool(_ENV_ENABLE)


def _use_tracemalloc() -> bool:
    return is_enabled() and _env_bool(_ENV_TRACE)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return -1.0


def _handles_and_threads() -> tuple:
    """Windows 句柄数与线程数：原生资源泄漏（PortAudio 流/FFmpeg 子进程/COM 等）的直接信号."""
    try:
        import psutil

        proc = psutil.Process()
        return proc.num_handles(), proc.num_threads()
    except Exception:
        return -1, -1


def _log_low_cost_sample(
    prev_counts=None, top: int = 15
) -> collections.Counter:
    """低成本采样：RSS / GC 对象数 / 活动任务 / 垃圾对象 / 句柄 / 线程数.

    额外统计 GC 对象类型分布 top-N 及相对上次采样的类型增量 top-N，
    用于在长时间运行中定位"每次对话/操作新增了什么对象类型"。
    """
    objs = gc.get_objects()
    counts = collections.Counter(type(o).__name__ for o in objs)

    logger.warning(
        "[内存采样] RSS=%.1fMB | 对象数=%d | 活动任务=%d | 垃圾对象=%d | 句柄=%d | 线程=%d",
        _rss_mb(),
        len(objs),
        len(asyncio.all_tasks()),
        len(gc.garbage),
        *_handles_and_threads(),
    )

    top_kv = ", ".join(f"{k}:{v}" for k, v in counts.most_common(top))
    logger.warning("[内存采样] 类型分布 top%d: %s", top, top_kv)

    if prev_counts:
        growing = [
            (k, v - prev_counts.get(k, 0))
            for k, v in counts.items()
            if v > prev_counts.get(k, 0)
        ]
        growing.sort(key=lambda kv: kv[1], reverse=True)
        delta_kv = ", ".join(f"+{k}:{v}" for k, v in growing[:top])
        logger.warning("[内存采样] 类型增量 top%d: %s", top, delta_kv)

    return counts


def _log_trace_top(snapshot, baseline, top: int) -> None:
    """相对启动基线的「净增长」：size_diff 正数=比启动时多占用的内存."""
    import tracemalloc

    top_stats = snapshot.compare_to(baseline, "lineno")
    growing = [s for s in top_stats if s.size_diff > 0]
    growing.sort(key=lambda s: s.size_diff, reverse=True)
    for s in growing[:top]:
        logger.warning(
            "[内存采样] +%.1fKB (+%d alloc) %s",
            s.size_diff / 1024,
            s.count_diff,
            s.traceback,
        )


async def _profiling_loop(interval: float, top: int, use_trace: bool):
    import tracemalloc

    baseline = None
    prev_counts = None
    if use_trace:
        gc.collect()
        baseline = tracemalloc.take_snapshot()
        logger.warning(
            "[内存采样] 已启动 tracemalloc（interval=%.0fs, top=%d）。调试模式，存在性能开销与 RSS 污染。",
            interval,
            top,
        )
    else:
        logger.warning(
            "[内存采样] 已启动低成本采样（interval=%.0fs）。未启用 tracemalloc，RSS 读数干净。",
            interval,
        )

    while True:
        await asyncio.sleep(interval)

        gc.collect()
        prev_counts = _log_low_cost_sample(prev_counts=prev_counts, top=top)
        if use_trace:
            _log_trace_top(tracemalloc.take_snapshot(), baseline, top)


def start() -> bool:
    """在当前事件循环中启动内存采样（若启用）。返回是否已启动。"""
    if not is_enabled():
        return False
    if _use_tracemalloc():
        import tracemalloc

        if tracemalloc.is_tracing():
            logger.warning("[内存采样] tracemalloc 已在运行，跳过重复启动")
            return False

    try:
        interval = float(os.environ.get(_ENV_INTERVAL, "60"))
    except ValueError:
        interval = 60.0
    try:
        top = int(os.environ.get(_ENV_TOP, "15"))
    except ValueError:
        top = 15

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[内存采样] 无运行中的事件循环，无法启动")
        return False

    use_trace = _use_tracemalloc()
    if use_trace:
        import tracemalloc

        tracemalloc.start(10)  # 每个分配点保留 10 层调用栈
    asyncio.create_task(_profiling_loop(interval, top, use_trace))
    return True