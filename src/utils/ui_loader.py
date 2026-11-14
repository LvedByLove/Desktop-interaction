"""用 QUiLoader 实现 ``uic.loadUi(path, self)`` 的等价行为.

PySide6 不提供 ``PySide6.uic``。这里加载 .ui 后，把顶层布局转移到目标控件，
因此目标控件仍然可以用 ``findChild(...)`` 访问所有子控件。
"""

from pathlib import Path

from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget

from src.views.components.arrow_combo_box import ArrowComboBox
from src.views.components.toggle_switch import ToggleSwitch


def load_ui_into(ui_path: str | Path, target: QWidget) -> QWidget:
    """把 .ui 的内容加载到 target 控件上，并返回临时顶层控件（随后删除）."""
    loader = QUiLoader()
    # .ui 里声明的自定义控件必须提前注册
    loader.registerCustomWidget(ToggleSwitch)
    loader.registerCustomWidget(ArrowComboBox)

    loaded = loader.load(str(ui_path), target)
    if loaded is None:
        raise RuntimeError(f"无法加载 UI 文件: {ui_path}")

    # 复制顶层窗口属性（标题/最小尺寸），布局随后转移
    title = loaded.windowTitle()
    if title:
        target.setWindowTitle(title)
    minimum = loaded.minimumSize()
    if not minimum.isEmpty():
        target.setMinimumSize(minimum)

    layout = loaded.layout()
    if layout is not None:
        target.setLayout(layout)

    # loaded 控件本身已被掏空，延迟删除避免影响刚转移的布局
    loaded.deleteLater()
    return loaded
