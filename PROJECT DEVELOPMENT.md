## ⚡ 快速开始

> ⚠️注意：本项目更偏向 Windows 桌面场景。

---

### 1️⃣ 直接下载安装（Windows 用户）

访问 GitHub Releases：

<a href="https://github.com/LvedByLove/Desktop-interaction/releases">https://github.com/LvedByLove/Desktop-interaction/releases</a>

下载后双击启动安装即可安装使用。

---

### 2️⃣ 源码运行（开发者）
### 🧩 环境要求

| 项 | 要求 |
| :--- | :--- |
| <a href="https://www.python.org/downloads/windows/">Python</a> | 3.9+ |
| <a href="https://ffmpeg.org/download.html">ffmpeg</a> |9.0.2（推荐版本）|
| <a href="ms-settings:about" data-linktype="external">系统</a> | Windows 10+ / Linux |

> ⚠️本项目使用<a href="https://ffmpeg.org/download.html">ffmpeg</a>进行音乐编码，建议下载<a href="https://ffmpeg.org/download.html">ffmpeg</a>开发效果更完整
#### 创建虚拟环境

**Windows：**

```bash
py -m venv .venv
```

**macOS / Linux：**

```bash
python3 -m venv .venv
```

#### 激活虚拟环境

**Windows（CMD）：**

```bash
.venv\Scripts\activate
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
> .\.venv\Scripts\Activate.ps1
> ```

**macOS / Linux：**

```bash
source .venv/bin/activate
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