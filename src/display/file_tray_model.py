# -*- coding: utf-8 -*-
"""文件中转站：数据源 + 系统文件图标供给器.

仅进程内暂存，不持久化到磁盘。路径列表通过 QAbstractListModel 暴露给 QML，
每个条目提供 path（本地路径）与 icon（图标 PNG 的 file:/// URL）两个角色。
系统文件/文件夹图标由 QFileIconProvider 生成，落盘为临时 PNG 后以 file:/// URL
供 QML 直接加载 —— 绕开 QQuickImageProvider 跨版本虚方法签名不稳定的问题。
"""

import hashlib
import os
import tempfile

from PyQt5.QtCore import (
    QAbstractListModel,
    QFileInfo,
    QModelIndex,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QFileIconProvider


class FileIconProvider:
    """生成系统文件/文件夹图标，缓存为写盘 PNG 的 file:/// URL。

    QFileIconProvider 依赖 QApplication（平台主题），故 QFileIconProvider 延迟到
    首次请求图标时才创建；把图标提前落盘为 PNG，避免 QML 渲染线程重复做耗时的
    shell 图标查询。
    """

    def __init__(self, icon_size=64):
        self._provider = None
        self._icon_size = icon_size
        self._pixmap_cache = {}
        self._url_cache = {}
        self._icon_dir = os.path.join(
            tempfile.gettempdir(), "xiaozhi_file_tray_icons"
        )

    def _ensure_provider(self):
        if self._provider is None:
            self._provider = QFileIconProvider()
        return self._provider

    def pixmap_for_path(self, path):
        """返回路径对应的 QPixmap 图标（供中转站面板显示与落盘 PNG 使用）。"""
        if path in self._pixmap_cache:
            return self._pixmap_cache[path]
        icon = self._ensure_provider().icon(QFileInfo(path))
        if icon.isNull():
            pixmap = QPixmap(self._icon_size, self._icon_size)
            pixmap.fill(Qt.transparent)
        else:
            pixmap = icon.pixmap(self._icon_size, self._icon_size)
        self._pixmap_cache[path] = pixmap
        return pixmap

    def drag_pixmap_for_path(self, path, size=32):
        """返回路径对应的小尺寸 QPixmap 图标（供拖拽光标使用）。

        单独用小尺寸（默认 32），避免直接复用车载大图标（64）导致拖拽图像
        明显大于系统资源管理器的拖拽光标。
        """
        icon = self._ensure_provider().icon(QFileInfo(path))
        if icon.isNull():
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
        else:
            pixmap = icon.pixmap(size, size)
        return pixmap

    def icon_url_for_path(self, path):
        """返回路径对应图标的 file:/// URL（PNG 落盘 + 缓存）。"""
        if path in self._url_cache:
            return self._url_cache[path]

        url = ""
        pixmap = self.pixmap_for_path(path)
        if not pixmap.isNull():
            try:
                os.makedirs(self._icon_dir, exist_ok=True)
                name = hashlib.md5(path.encode("utf-8")).hexdigest() + ".png"
                png_path = os.path.join(self._icon_dir, name)
                if not os.path.exists(png_path):
                    pixmap.save(png_path, "PNG")
                url = QUrl.fromLocalFile(png_path).toString()
            except Exception:
                url = ""
        self._url_cache[path] = url
        return url


class FileTrayModel(QAbstractListModel):
    """中转站数据源：维护暂存的文件/文件夹本地路径列表。

    用 QAbstractListModel 暴露给 QML（避免 QStringList 属性绑定造成的
    Repeater model 绑定循环），并提供 path / icon 两个角色。
    """

    PathRole = Qt.UserRole + 1
    IconRole = Qt.UserRole + 2
    SelectedRole = Qt.UserRole + 3

    # 内容变化通知（供 QML 刷新尺寸 / 空态自动关闭）
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = []
        self._icon_provider = None
        # 多选状态：当前选中的行索引集合 + shift 连续选中的锚点
        self._selection = set()
        self._anchor = None

    def set_icon_provider(self, provider):
        self._icon_provider = provider

    @property
    def paths(self):
        """当前所有暂存路径（Python 侧内部使用）。"""
        return self._paths

    # --- QAbstractListModel 接口 ---

    def roleNames(self):
        return {
            self.PathRole: b"path",
            self.IconRole: b"icon",
            self.SelectedRole: b"selected",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._paths)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._paths)):
            return None
        path = self._paths[index.row()]
        if role == self.PathRole:
            return path
        if role == self.IconRole:
            if self._icon_provider is None:
                return ""
            return self._icon_provider.icon_url_for_path(path)
        if role == self.SelectedRole:
            return index.row() in self._selection
        return None

    # --- 增删改 ---

    def add_paths(self, paths):
        """新增路径（自动去重、保持插入顺序）。返回实际新增的路径列表。"""
        existing = set(self._paths)
        added = []
        for p in paths:
            if p and p not in existing:
                existing.add(p)
                added.append(p)
        if not added:
            return []

        start = len(self._paths)
        self.beginInsertRows(QModelIndex(), start, start + len(added) - 1)
        self._paths.extend(added)
        self.endInsertRows()
        self.changed.emit()
        return added

    def remove_index(self, index):
        """按索引移除。返回是否成功。"""
        if 0 <= index < len(self._paths):
            self.beginRemoveRows(QModelIndex(), index, index)
            del self._paths[index]
            self.endRemoveRows()
            # 同步多选状态：被删项丢弃，其后的索引左移一位
            self._selection = {
                (s if s < index else s - 1) for s in self._selection if s != index
            }
            if self._anchor is not None:
                if self._anchor == index:
                    self._anchor = None
                elif self._anchor > index:
                    self._anchor -= 1
            self.changed.emit()
            return True
        return False

    def clear(self):
        """清空所有暂存路径（关闭中转站 / 程序退出前调用）。"""
        if self._paths:
            self.beginResetModel()
            self._paths = []
            self.endResetModel()
            self.changed.emit()
        if self._selection or self._anchor is not None:
            self._selection.clear()
            self._anchor = None

    # --- 多选操作 ---

    def _notify_selection(self):
        """选中状态变化后通知 QML 刷新 selected 角色。"""
        n = len(self._paths)
        if n:
            self.dataChanged.emit(self.index(0, 0), self.index(n - 1, 0), [self.SelectedRole])

    def selected_indexes(self):
        """返回当前选中的行索引（升序）。"""
        return sorted(self._selection)

    def clear_selection(self):
        """清空选中（点击空白等场景）。"""
        if not self._selection and self._anchor is None:
            return
        self._selection.clear()
        self._anchor = None
        self._notify_selection()

    def select_only(self, index):
        """单选：仅选中指定项，并更新 shift 锚点。"""
        index = int(index)
        if not (0 <= index < len(self._paths)):
            return
        self._selection = {index}
        self._anchor = index
        self._notify_selection()

    def toggle_selected(self, index):
        """Ctrl 点击：切换指定项的选中状态，并更新锚点。"""
        index = int(index)
        if not (0 <= index < len(self._paths)):
            return
        if index in self._selection:
            self._selection.discard(index)
        else:
            self._selection.add(index)
        self._anchor = index
        self._notify_selection()

    def select_range_to(self, index):
        """Shift 点击：从锚点连续选中到指定项（不含其它项）。"""
        index = int(index)
        n = len(self._paths)
        if n == 0 or not (0 <= index < n):
            return
        anchor = self._anchor if self._anchor is not None else 0
        if not (0 <= anchor < n):
            anchor = 0
        lo, hi = (anchor, index) if anchor <= index else (index, anchor)
        self._selection = set(range(lo, hi + 1))
        self._notify_selection()

    def select_indices(self, indices):
        """框选：将选中集替换为给定索引集合，并更新锚点为其中第一项。"""
        try:
            idx = [int(i) for i in indices]
        except Exception:
            return
        n = len(self._paths)
        idx = [i for i in idx if 0 <= i < n]
        self._selection = set(idx)
        if idx:
            self._anchor = idx[0]
        self._notify_selection()