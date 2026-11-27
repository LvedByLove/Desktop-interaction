# 🗂 项目目录结构

> 本文件说明 Desktop-interaction 的目录组织与各模块职责。
> 项目基于 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 二次开发，目录命名沿用了上游风格。

```text
Desktop-interaction/
├── main.py                       # 程序入口：Qt 环境变量、单实例检测、启动 Application
├── src/
│   ├── application.py            # 应用主类：装配协议 / 显示层 / 插件，驱动对话状态流转
│   ├── audio_codecs/             # 音频编解码：Opus 收发、AEC 回声消除、本地音乐解码
│   ├── audio_processing/         # 唤醒词检测（sherpa-onnx）
│   ├── constants/                # 常量与系统参数（状态枚举、音频配置）
│   ├── core/                     # 核心基础设施：OTA 版本检查 / 激活、系统初始化流程
│   ├── display/                  # 桌面显示层
│   │   ├── base_display.py       # 显示层抽象基类
│   │   ├── gui_display.py        # GUI 主显示：胶囊状态、文件拖放链路、窗口联动
│   │   ├── gui_display.qml       # 胶囊 / 薄条 / 音乐 / 历史等主界面 QML
│   │   ├── gui_display_model.py  # 供 QML 绑定的属性模型
│   │   ├── input_display.py      # 屏幕顶部文字输入框窗口
│   │   ├── input_display.qml     #
│   │   ├── cli_display.py        # CLI 模式显示
│   │   ├── file_drag_watcher.py  # pynput 全局“疑似拖拽”粗检测
│   │   ├── ole_drop_catcher.py   # Windows OLE 原生拖放捕捉（CF_HDROP 精确判定）
│   │   ├── file_tray_model.py    # 文件中转站数据源与图标角色
│   │   ├── file_thumbnail_provider.py # Shell 内容缩略图：后台取图 + 回退 + 缓存
│   │   ├── file_tray_window.qml  # 中转站面板 QML
│   │   ├── media_control_model.py # 右键展开的播放面板数据模型
│   │   └── region_capture_overlay.py  # 屏幕区域框选截图浮层
│   ├── iot/                      # IoT 设备抽象（Thing / Property / Method）与设备管理
│   │   └── things/               # 具体设备实现（如 lamp 台灯）
│   ├── mcp/                      # MCP 工具集
│   │   ├── mcp_server.py         # MCP 服务端与工具注册
│   │   ├── calendar/             # 日程与提醒
│   │   ├── camera/               # 摄像头与视觉识别
│   │   ├── file_tools/           # 文件操作
│   │   ├── media/                # 系统媒体会话（SMTC）：外部播放器读取/控制 + 歌词
│   │   ├── music/                # 在线 / 本地音乐播放
│   │   ├── screenshot/           # 截图
│   │   ├── system/               # 系统控制（音量等）
│   │   └── timer/                # 定时器
│   ├── network/                  # MQTT 客户端
│   ├── plugins/                  # 插件系统与各功能插件（音频 / UI / MCP / IoT / 快捷键 / 唤醒词 / 日历 / 媒体会话）
│   ├── protocols/                # 通信协议：WebSocket / MQTT
│   ├── utils/                    # 通用工具：配置、日志、资源查找、音量 / 亮度、设备激活、Qt 兼容等
│   └── views/                    # PySide6 窗口与界面
│       ├── activation/           # 设备激活界面（GUI + QML + CLI）
│       ├── base/                 # 窗口基类与异步 mixin
│       ├── components/           # 通用控件（系统托盘、开关、下拉框等）
│       ├── settings/             # 设置窗口及分类页（音频 / 摄像头 / 唤醒词 / MCP / 数据目录…）
│       └── theme.py              # 主题与配色
├── assets/                       # 图标、截图、提示音等静态资源
├── config/                       # 默认配置（config.json、efuse.json）
├── libs/                         # 原生动态库（libopus、webrtc_apm）
├── models/                       # 唤醒词模型文件（tokens.txt 等）
├── scripts/                      # 开发辅助脚本（相机扫描、日历查询、目录树生成等）
├── installer/                    # Windows 安装包构建脚本（NSIS / Inno Setup）
├── requirements.txt              # Python 依赖
└── README.md
```

> `dist/`、`cache/`、`nuitka_build*/`、`__pycache__/` 等为构建或运行生成目录，不属于源码结构。

---

## 日志等级说明

项目使用 Python 标准 `logging`，等级由低到高依次为：

| 等级 | 数值 | 用途 |
| :--- | :--- | :--- |
| `DEBUG` | 10 | 调试细节（高频：拖拽开始/结束、OLE 命中目标区等） |
| `INFO` | 20 | 常规运行信息（启动完成、文件接收成功等） |
| `WARNING` | 30 | 可恢复的异常（显示/隐藏捕捉窗口失败、拖放解析失败等） |
| `ERROR` | 40 | 功能失败（组件初始化失败、注册拖放失败等） |
| `CRITICAL` | 50 | 致命错误 |

**当前默认**：根日志记录器、控制台处理器、文件处理器均设置为 `INFO`，即只输出 `INFO / WARNING / ERROR / CRITICAL`，`DEBUG` 级别的日志（例如“疑似拖拽开始：显示 OLE 捕捉窗口”）默认不显示；第三方库（urllib3、websockets、asyncio、qasync、paho、PIL、sounddevice、matplotlib）统一压制到 `WARNING`。

**查看指定等级或全部日志**：修改 `src/utils/logging_config.py` 中以下三处 `setLevel` 即可：

| 位置 | 常量 | 作用 |
| :--- | :--- | :--- |
| `setup_logging()` 内 `root_logger.setLevel(logging.INFO)` | 第 153 行 | 全局最低级别 |
| `console_handler.setLevel(logging.INFO)` | 第 162 行 | 控制台输出级别 |
| `file_handler.setLevel(logging.INFO)` | 第 181 行 | 日志文件输出级别 |

- **显示全部日志（含 DEBUG）**：三处都改为 `logging.DEBUG`
- **显示警告及以上**：三处都改为 `logging.WARNING`
- **只显示错误**：三处都改为 `logging.ERROR`

> 三处需要一起改：根记录器决定“是否产生”日志，处理器级别决定“是否输出”。

**日志文件位置**：
- Windows：`%LOCALAPPDATA%\<项目名>\logs\app.log`
- macOS：`~/Library/Application Support/<项目名>/logs/app.log`
- Linux：`~/.local/share/<项目名>/logs/app.log`

日志每天午夜切割、保留 30 天；可通过环境变量 `XIAOZHI_LOG_DIR` 或运行时配置 `PATHS.LOG_DIR` 指定其它目录（不可写时回退到项目根目录 `logs/`）。
