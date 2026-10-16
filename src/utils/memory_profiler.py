# -*- coding: utf-8 -*-
"""
低成本内存采样诊断器（默认关闭，仅调试用）。

启用方式（环境变量）：
    XIAOZHI_MEM_PROFILE=1          # 开启采样
    XIAOZHI_MEM_INTERVAL=60        # 采样间隔（秒），默认 60
    XIAOZHI_MEM_TOP=15             # 每次输出增长最多的前 N 个分配点，默认 15

输出：周期记录 RSS、GC 对象数、活动 asyncio 任务数，以及 tracemalloc
统计的「累计净增长」最多的 Python 分配位置。净增长（相对启动时的基线
快照）正是长时间运行泄漏的信号：某处只增不减，就是漏点所在。

说明：诊断模式在事件循环线程上做 gc.collect() 与 tracemalloc 快照，
采样瞬间可能存在短暂界面卡顿，属可接受的调试代价，日常运行请勿开启。
"""

import asyncio
import gc
import logging
import os
import tracemalloc

logger = logging.getLogger("memory_profiler")

_ENV_ENABLE = "XIAOZHI_MEM_PROFILE"
_ENV_INTERVAL = "XIAOZHI_MEM_INTERVAL"
_ENV_TOP = "XIAOZHI_MEM_TOP"


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    return _env_bool(_ENV_ENABLE)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return -1.0


async def _profiling_loop(interval: float, top: int):
    gc.collect()
    baseline = tracemalloc.take_snapshot()
    logger.warning(
        "[内存采样] 已启动 tracemalloc（interval=%.0fs, top=%d）。调试模式，存在性能开销。",
        interval,
        top,
    )

    while True:
        await asyncio.sleep(interval)

        gc.collect()
        snapshot = tracemalloc.take_snapshot()

        logger.warning(
            "[内存采样] RSS=%.1fMB | 对象数=%d | 活动任务=%d | 垃圾对象=%d",
            _rss_mb(),
            len(gc.get_objects()),
            len(asyncio.all_tasks()),
            len(gc.garbage),
        )

        # 相对启动基线的「净增长」：size_diff 正数=比启动时多占用的内存
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


def start() -> bool:
    """在当前事件循环中启动内存采样（若启用）。返回是否已启动。"""
    if not is_enabled():
        return False
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

    tracemalloc.start(10)  # 每个分配点保留 10 层调用栈
    asyncio.create_task(_profiling_loop(interval, top))
    return True