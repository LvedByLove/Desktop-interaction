# <img src="https://github.com/LvedByLove/Desktop-interaction/blob/main/assets/image/Redstone.png">Desktop-interaction

> 桌面胶囊 · AI 助手 | 基于 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 的桌面交互增强版小智语音客户端。

<p align="center">
  <a href="https://github.com/LvedByLove/Desktop-interaction">
    <img src="https://img.shields.io/badge/GitHub-Desktop--interaction-blue?style=flat-square&logo=github" alt="GitHub"/>
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"/>
  </a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue?style=flat-square&logo=python" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square" alt="Platform"/>
  <img src="https://img.shields.io/badge/Status-Active-success?style=flat-square" alt="Status"/>
</p>

<p align="center">
  一个更贴合桌面使用习惯的小智语音客户端 ✨<br/>
  悬浮胶囊 · 音乐播放 · 歌词显示 · TTS 与音乐共存 · 文件中转站
</p>

---

## 📖 项目简介

**Desktop-interaction** 是一个使用 Python 实现的桌面端小智语音客户端。本项目基于开源项目 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 进行二次开发，主要针对 **桌面端交互体验、操作界面、音乐播放体验、本地操作逻辑和文件中转** 做了优化和新增。

原版 `py-xiaozhi` 已经提供了完整的小智语音客户端能力，包括语音对话、TTS 播报、MCP 工具、音乐播放、日程、摄像头识别等功能。本项目在此基础上，更偏向 **“桌面常驻助手”** 的使用体验，希望让它在日常电脑使用中更加自然、轻量、少打扰。

一个属于你的桌面 AI 助手，可以让其播放音乐、打开软件、进行智能问答、屏幕截图问答等；定位 Windows 电脑桌面辅助人工智能。

> ⚠️ 本项目不是 `py-xiaozhi` 官方版本，而是个人基于使用体验进行调整和增强的 **非官方二次创作版本**。

---

## 🔗 项目来源

本项目基于以下开源项目二次开发：

| 类型 | 项目 | 说明 |
| :--- | :--- | :--- |
| 原项目 | [`py-xiaozhi`](https://github.com/huangjunsen0406/py-xiaozhi) | 基础项目 |
| 原作者 | [`huangjunsen0406`](https://github.com/huangjunsen0406) | 项目作者 |
| 上游项目 | [`xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32) | 相关上游 |
| 本项目 | [`LvedByLove/Desktop-interaction`](https://github.com/LvedByLove/Desktop-interaction) | 二次开发仓库 |

🙏 感谢原作者和社区提供的基础项目。没有原项目的基础工作，本项目无法完成。

---

## 🚀 主要改动

本项目基于[`py-xiaozhi`](https://github.com/huangjunsen0406/py-xiaozhi) 项目1.1.8版本（2025/8/19）开始二次创作，也是那年我接触小智，再次感谢您让我可以有一个自己喜欢的桌面互动AI。
相比原版，本项目主要做了以下方向的修改。

### 1️⃣ 桌面悬浮交互界面

<p align="center">
  <img src="https://img2024.cnblogs.com/blog/3857670/202610/3857670-20261013092034550-302858640.png" alt="Desktop-interaction 悬浮胶囊界面截图"/>
</p>

- 重做桌面交互界面，做悬浮胶囊界面更灵动
- 使用黑色胶囊风格减少常驻窗口对桌面操作的干扰
- 支持悬浮展开状态、不使用自动隐藏。
- 优化 TTS、聆听状态、输入框、控制按钮的显示方式
- 点击悬浮球可以弹出输入框，即使因环境无法说话也能交互
- 新增可自动打开网页、网站
- 设计目标是：你几乎感觉不到它在那里，但需要的时候一叫就出来

### 2️⃣ 界面隐藏及历史对话显示

- 增加了在音乐播放状态点击可折叠拖动位置，减少桌面遮挡；再次点击可以恢复完整歌词
- 同样也对tts播报界面做了一样的防遮挡功能
- 增加双击播报界面显示历史及现在文本窗口，让历史可记
- 文本输入更轻便，不打扰：输入框显示在屏幕正上方，通过点击处于[圆球]状态触发或者快捷键触发


### 3️⃣ TTS 与音乐共存优化

-  TTS 播报时不再粗暴中断本地音乐播放
-  对话时使用 ducking 方式降低本地音乐播放的音量，TTS 结束后恢复本地音乐播放的音量
-  只对本地音乐播放状态生效，后端调用搜索的音乐不生效

### 4️⃣ 文件中转站

<p align="center">
  <img src="https://img2024.cnblogs.com/blog/3857670/202610/3857670-20261013092152209-2086920598.png" alt="Desktop-interaction 文件中转站界面截图"/>
</p>

-  可以把日常频繁移动、复制的文件 / 文件夹放置到中转站
-  不需要频繁切换窗口也可以实现文件位置交换
-  适合桌面文件整理、批量移动、多窗口操作场景


---

## ✨ 新功能特性


- 支持歌词显示与折叠，让选择更多样
- 支持桌面悬浮胶囊界面，让喜欢不在只存在我的幻想
- 支持界面自动隐藏，减少桌面干扰，让工具成为工具
- 支持文件中转站，减少窗口切换文件移动，让工具不再只是对话
- 文字输入加强 / 输入框单独显示，让互动不再干扰
- 支持打开网站 / 不再只打开浏览器让用户手动输入网址
---

## ⚡ 快速开始

### 🧩 环境要求

| 项 | 要求 |
| :--- | :--- |
| Python | 3.9+ |
| 系统 | Windows 10+ / macOS 10.15+ / Linux |

> ⚠️注意：本项目更偏向 Windows 桌面场景。

---

### 1️⃣ 直接下载安装（推荐 Windows 用户）

访问 GitHub Releases：

```text
https://github.com/LvedByLove/Desktop-interaction/releases
```

或直接下载安装包：

```text
https://github.com/LvedByLove/Desktop-interaction/releases/download/Installer-nuitka2.12.14/Desktop.interaction-setup-nuitka2.12.14.exe
```

双击启动安装即可使用。

---

### 2️⃣ 源码运行（开发者）

#### 创建虚拟环境

**Windows：**

```bash
py -m venv venv
```

**macOS / Linux：**

```bash
python3 -m venv venv
```

#### 激活虚拟环境

**Windows（CMD）：**

```bash
venv\Scripts\activate
```

> 如果 PowerShell 报权限错误，先执行：
>
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
> ```
>
> 然后再激活：
>
> ```powershell
> .\venv\Scripts\Activate.ps1
> ```

**macOS / Linux：**

```bash
source venv/bin/activate
```

#### 升级 pip（可选，推荐）

```bash
python -m pip install --upgrade pip
```

#### 安装依赖

```bash
pip install -r requirements.txt
```

#### 运行程序

```bash
# GUI 模式（默认）
python main.py

# CLI 模式
python main.py --mode cli
```

---

## 🎯 项目定位

本项目更偏向于：

-  **桌面常驻 AI 助手**
-  **小智语音客户端的桌面体验增强版**
-  适合希望在 PC 上体验小智语音交互、MCP 工具和桌面浮窗交互的用户
-  定位 Windows 电脑桌面辅助人工智能

本项目 **不是从零开发的独立项目**，而是在 `py-xiaozhi` 的基础上做的二次开发版本。

如果你想要的是一个功能大而全、界面复杂的桌面 AI 客户端，它可能不是最合适的选择。  
但如果你想要的是一颗安静待在桌面角落、叫它才出来、用完就消失的语音助手，它的设计思路会非常对味。

---

## 📦 发布说明

如果你从本项目继续修改或分发，请注意：

- ✅ 再分发时，请保留原始版权声明和 MIT 许可证文本。原项目许可证见 [LICENSE-py-xiaozhi](LICENSE-py-xiaozhi)。
- ✅ 保留本项目版权声明
- ✅ 明确说明项目来源于 `py-xiaozhi`和`Desktop-interaction`
- ⚠️ 注意：不要将本项目表述为 `py-xiaozhi` 官方版本

---

## 📄 许可协议

本项目基于 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) 的 **MIT License** 进行二次开发。

原项目版权归原作者所有：Copyright (c) 2025 Junsen。

本项目新增和修改的代码同样按 **MIT License** 发布，除非特别说明。

- 本项目主许可证：[LICENSE](LICENSE)
- 原项目许可证副本：[LICENSE-py-xiaozhi](LICENSE-py-xiaozhi)

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

This project is a modified version of [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi). The original MIT License is preserved in [LICENSE-py-xiaozhi](LICENSE-py-xiaozhi).

Original copyright (c) 2025 Junsen.  
Modifications copyright (c) 2026 LvedByLove.

请在使用、修改、分发本项目时保留原始版权声明和许可证文件。

---

## 🙏 致谢

感谢以下项目和作者：

- [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)
- py-xiaozhi 原作者及贡献者
- 小智开源社区

---

## ⚠️ 免责声明

本项目为个人学习和体验优化用途的 **非官方二次开发版本**。项目中涉及的小智服务、协议、第三方接口、音乐服务等能力，请遵守对应服务提供方的使用条款所说明的使用要求。若上游项目、接口服务或相关权利方对项目说明、署名方式或分发方式有异议，请联系后进行调整。

---

<p align="center">
  <sub>Made with ❤️ for the 小智 community</sub>
</p>
