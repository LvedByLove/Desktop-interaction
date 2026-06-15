# Desktop interaction

> 基于 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 的桌面交互增强版小智语音客户端。

<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"/>
  </a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue?style=flat-square&logo=python" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square" alt="Platform"/>
</p>

## 项目简介

Desktop interaction 是一个使用 Python 实现的桌面端小智语音客户端。本项目基于开源项目 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 进行二次开发，主要针对桌面端的交互体验、悬浮界面、音乐播放体验和本地操作逻辑做了优化。

原版 `py-xiaozhi` 已经提供了完整的小智语音客户端能力，包括语音对话、TTS 播报、MCP 工具、音乐播放、日程、摄像头识别等功能。本项目在此基础上，更偏向“桌面常驻助手”的使用体验，希望让它在日常电脑使用中更加自然、轻量、少打扰。

> 本项目不是 `py-xiaozhi` 官方版本，而是个人基于使用体验进行调整和增强的非官方二次创作版本。

## 项目来源

本项目基于以下开源项目二次开发：

- 原项目：[`py-xiaozhi`](https://github.com/huangjunsen0406/py-xiaozhi)
- 原作者：[`huangjunsen0406`](https://github.com/huangjunsen0406)
- 原项目许可证：MIT License
- 上游相关项目：[`xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32)
- 音乐接口：[`NekoMusicDocs`](https://github.com/FantasyNetworkCN/NekoMusicDocs)

感谢原作者和社区提供的基础项目。没有原项目的基础工作，本项目无法完成。

## 主要改动

相比原版，本项目主要做了以下方向的修改。

### 1. 桌面悬浮交互界面优化

- 重做桌面端悬浮胶囊界面；
- 使用类似“灵动岛”的黑色胶囊风格；
- 支持悬浮展开、自动隐藏、顶部贴边隐藏；
- 减少常驻窗口对桌面操作的干扰；
- 优化 TTS、聆听状态、输入框、控制按钮的显示方式。

### 2. 音乐播放体验优化

- 音乐搜索、歌词与播放接口参考 [`NekoMusicDocs`](https://github.com/FantasyNetworkCN/NekoMusicDocs) 进行接入；
- 优化音乐搜索与播放流程；
- 修复首次播放未缓存歌曲时，因为下载时间较长导致服务端误判失败的问题；
- 增加后台缓存后自动播放逻辑；
- 修复暂停后继续播放时音频队列堵塞、解码器持续写入导致超时的问题；
- 暂停时停止解码器，恢复时从暂停位置重新解码播放；
- 支持播放、暂停、继续、停止等音乐指令的本地兜底识别。

### 3. 本地音乐命令兜底

为避免服务端 LLM 只“口头答应”但没有真正调用 MCP 工具，本项目增加了本地 STT 指令兜底。

支持示例：

```text
播放起风了
播放李荣浩的戒烟
听一下晴天
暂停音乐
继续播放
恢复音乐
停止音乐
结束播放
关闭音乐
```

当识别到这些明确的音乐控制语句时，本地会直接调用对应播放器逻辑，而不是完全依赖云端是否触发 MCP 工具。

### 4. 歌词显示优化

- 播放音乐时支持歌词显示；
- 歌词界面可以点击折叠；
- 折叠后只保留一个小型跳动动画，表示音乐仍在播放；
- 再次点击可以恢复完整歌词；
- 当前台应用全屏时，可自动折叠歌词界面，减少遮挡。

### 5. TTS 与音乐共存优化

- TTS 播报时不再粗暴中断音乐；
- 使用 ducking 方式降低音乐音量；
- TTS 结束后恢复音乐音量；
- 真实 TTS 与音乐歌词显示互不抢占。

### 6. 其他体验修复

- 优化 MCP 工具调用反馈；
- 改善本地 UI 状态显示；
- 修复部分异步任务、音频解码、播放队列相关问题；
- 减少语音交互中“说执行了但实际上没执行”的情况。

## 功能特性

- Python 桌面端小智语音客户端；
- 支持 GUI / CLI 模式；
- 支持语音识别、TTS 播报、连续对话；
- 支持 MCP 工具调用；
- 支持音乐搜索、播放、暂停、继续、停止；
- 支持歌词显示与折叠；
- 支持日程、计时器、摄像头识别、截图分析等能力；
- 支持桌面悬浮胶囊界面；
- 支持自动隐藏，减少桌面干扰。

## 快速开始

### 环境要求

- Python 3.9+
- Windows 10+/macOS 10.15+/Linux

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行程序

```bash
# GUI 模式（默认）
python main.py

# CLI 模式
python main.py --mode cli
```

## 项目定位

本项目更偏向于：

- 桌面常驻 AI 助手；
- 小智语音客户端的桌面体验增强版；
- 适合希望在 PC 上体验小智语音交互、MCP 工具和桌面浮窗交互的用户。

本项目不是从零开发的独立项目，而是在 `py-xiaozhi` 的基础上做的二次开发版本。

## 发布说明

如果你从本项目继续修改或分发，请注意：

- 保留原始 `LICENSE` 文件；
- 保留原项目版权声明；
- 明确说明项目来源于 `py-xiaozhi`；
- 不要将本项目表述为 `py-xiaozhi` 官方版本；
- 发布前请确认不要上传个人配置、Token、日志、缓存和虚拟环境目录。

## 许可协议

本项目基于 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 的 MIT License 进行二次开发。

原项目版权归原作者所有：

```text
Copyright (c) 2025 Junsen
```

本项目保留原项目的 MIT License 授权文本。本项目中新增和修改的代码同样按 MIT License 发布，除非特别说明。

请在使用、修改、分发本项目时保留原始版权声明和许可证文件。

## 致谢

感谢以下项目和作者：

- [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)
- [NekoMusicDocs](https://github.com/FantasyNetworkCN/NekoMusicDocs)
- py-xiaozhi 原作者及贡献者
- 小智开源社区

## 免责声明

本项目为个人学习和体验优化用途的非官方二次开发版本。项目中涉及的小智服务、协议、第三方接口、音乐服务等能力，请遵守对应服务提供方的使用条款。音乐搜索、歌词和播放相关接口请同时遵守相关接口服务和 [`NekoMusicDocs`](https://github.com/FantasyNetworkCN/NekoMusicDocs) 所说明的使用要求。若上游项目、接口服务或相关权利方对项目说明、署名方式或分发方式有异议，请联系后进行调整。
