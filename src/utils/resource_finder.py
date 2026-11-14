# resource_finder.py
from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

PathLike = Union[str, Path]
_MANIFEST_CANDIDATES = ("unifypy.json", "app.json", "package.json")

_logger = logging.getLogger(__name__)

# 可写目录环境变量（优先级最高，便于便携版/调试）
ENV_DATA_DIR = "XIAOZHI_DATA_DIR"
ENV_CACHE_DIR = "XIAOZHI_CACHE_DIR"
ENV_LOG_DIR = "XIAOZHI_LOG_DIR"
ENV_MUSIC_CACHE_DIR = "XIAOZHI_MUSIC_CACHE_DIR"
ENV_KEYWORDS_DIR = "XIAOZHI_KEYWORDS_DIR"

# 运行时覆盖（由 apply_path_overrides_from_config 设置）
_override_cache: Optional[Path] = None
_override_log: Optional[Path] = None
_override_music: Optional[Path] = None
_override_keywords: Optional[Path] = None
_override_mcp_plugins: Optional[Path] = None


def _env_path(name: str) -> Optional[Path]:
    """读取路径环境变量；空值/非法值返回 None."""
    raw = os.environ.get(name)
    if not raw or not str(raw).strip():
        return None
    try:
        return Path(str(raw).strip()).expanduser().resolve()
    except Exception:
        return None


class ResourceFinder:
    """
    统一资源定位：开发、PyInstaller（onedir/onefile）、安装后皆可用。
    """

    _instance: "ResourceFinder" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initd", False):
            return
        self._initd = True

        # 运行基目录（_MEIPASS / exe_dir / project_root）
        self._base_dir = self._runtime_base_dir()

        # 加载元信息（manifest/Info.plist/环境变量）
        self._meta = self._load_app_meta(self._base_dir)
        self._app_name = self._derive_app_name(self._meta)

        # 构建搜索路径（有序、去重）
        self._search_dirs = self._build_search_dirs()

    # -------------- 公共 API --------------

    def get_app_meta(self) -> Dict:
        """
        返回应用元信息。
        """
        return dict(self._meta)

    def get_app_name(self) -> str:
        """
        返回应用名（manifest/display_name/name → .app 名 → exe/项目名）。
        """
        return self._app_name

    def get_project_root(self) -> Path:
        """
        开发态返回源码根；打包态返回运行基目录。
        """
        if not self._is_frozen():
            return self._detect_project_root(default=self._base_dir)
        return self._base_dir

    def get_user_data_dir(self, create: bool = True) -> Path:
        """
        用户数据（可写）目录。

        优先环境变量 XIAOZHI_DATA_DIR；否则按平台惯例。
        """
        env = _env_path(ENV_DATA_DIR)
        if env is not None:
            p = env
        else:
            home = Path.home()
            if sys.platform == "win32":
                p = home / "AppData" / "Local" / self._app_name
            elif sys.platform == "darwin":
                p = home / "Library" / "Application Support" / self._app_name
            else:
                p = home / ".local" / "share" / self._app_name
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def get_user_cache_dir(self, create: bool = True) -> Path:
        """缓存目录：环境变量 > 运行时覆盖 > {用户数据}/cache."""
        p = (
            _env_path(ENV_CACHE_DIR)
            or _override_cache
            or (self.get_user_data_dir(create=False) / "cache")
        )
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def get_user_log_dir(self, create: bool = True) -> Path:
        """日志目录：环境变量 > 运行时覆盖 > {用户数据}/logs."""
        p = (
            _env_path(ENV_LOG_DIR)
            or _override_log
            or (self.get_user_data_dir(create=False) / "logs")
        )
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def get_music_cache_dir(self, create: bool = True) -> Path:
        """音乐缓存：环境变量 > 运行时覆盖 > {缓存}/music."""
        p = (
            _env_path(ENV_MUSIC_CACHE_DIR)
            or _override_music
            or (self.get_user_cache_dir(create=False) / "music")
        )
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def get_keywords_dir(self, create: bool = True) -> Path:
        """唤醒词目录：环境变量 > 运行时覆盖 > {用户数据}/keywords."""
        p = (
            _env_path(ENV_KEYWORDS_DIR)
            or _override_keywords
            or (self.get_user_data_dir(create=False) / "keywords")
        )
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def get_mcp_plugins_dir(self, create: bool = True) -> Path:
        """外部 MCP 插件目录：运行时覆盖 > {用户数据}/mcp_plugins."""
        p = (
            _override_mcp_plugins
            or (self.get_user_data_dir(create=False) / "mcp_plugins")
        )
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def find_file(self, relpath: PathLike) -> Optional[Path]:
        """
        按相对路径查文件。
        """
        return self._find(relpath, want_dir=False)

    def find_directory(self, relpath: PathLike) -> Optional[Path]:
        """
        按相对路径查目录。
        """
        return self._find(relpath, want_dir=True)

    # 常用别名
    def find_models_dir(self) -> Optional[Path]:
        return self.find_directory("models")

    def find_assets_dir(self) -> Optional[Path]:
        return self.find_directory("assets")

    def find_config_dir(self) -> Optional[Path]:
        return self.find_directory("config")

    def find_libs_root(self) -> Optional[Path]:
        return self.find_directory("libs")

    # 在某个根（如 libs/models）之下继续拼接子路径查找（文件/目录）
    def find_under(
        self, root: str, *subparts: PathLike, want_dir: bool = True
    ) -> Optional[Path]:
        parts: List[str] = []
        for s in subparts:
            if s is None:
                continue
            parts.extend(Path(str(s)).parts)  # 兼容 "a/b" 合并字符串
        rel = Path(root, *parts)
        return self._find(rel, want_dir=want_dir)

    # -------------- 兼容老签名的便捷函数 --------------

    def find_libs_dir_compat(
        self, *parts: PathLike, system: str = None, arch: str = None
    ) -> Optional[Path]:
        """查找 libs 或其子目录。

        - 无参：返回 libs 根目录
        - 位置参数：find_libs_dir_compat('libopus', 'Darwin', 'arm64')
        - 关键字：find_libs_dir_compat(system='Darwin', arch='arm64')
        - 兼容旧写法：find_libs_dir_compat(f'libopus/{system_dir}', arch_name)
        """
        segs: List[PathLike] = []
        segs.extend(parts)
        if system:
            segs.append(system)
        if arch:
            segs.append(arch)
        if not segs:
            return self.find_under("libs", want_dir=True)
        return self.find_under("libs", *segs, want_dir=True)

    # -------------- 内部实现 --------------

    def _is_frozen(self) -> bool:
        return getattr(sys, "frozen", False)

    def _runtime_base_dir(self) -> Path:
        """
        统一运行基目录：优先 _MEIPASS，其次 exe_dir；开发态则回到项目根。
        """
        if self._is_frozen():
            return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        # 假设本文件位于 project/src/utils/resource_finder.py → parents[3] 是 project/
        return self._detect_project_root(default=Path(__file__).resolve().parents[2])

    def _detect_project_root(self, default: Path) -> Path:
        """
        向上查找带标志文件/目录的路径，尽量定位到 project/。
        """
        markers = {"assets", "models", "pyproject.toml", "requirements.txt", ".git"}
        p = default
        for parent in [p] + list(p.parents):
            try:
                entries = {e.name for e in parent.iterdir()}
            except Exception:
                continue
            if markers & entries:
                return parent.resolve()
        return default.resolve()

    def _load_app_meta(self, base: Path) -> Dict:
        """
        读取 manifest（unifypy.json/app.json/package.json）与 macOS Info.plist。环境变量可覆盖。
        """
        meta: Dict = {}

        # 环境变量可直接注入/覆盖
        for k in ("APP_NAME",):
            if os.getenv(k):
                meta["name"] = os.getenv(k)

        # 1) manifest（在 base 与 base.parent 中各找一层）
        candidates: List[Path] = []
        for name in _MANIFEST_CANDIDATES:
            candidates.append(base / name)
            candidates.append(base.parent / name)

        for c in candidates:
            try:
                if c.is_file():
                    meta.update(json.loads(c.read_text(encoding="utf-8")))
                    meta["_manifest_path"] = str(c)
                    break
            except Exception:
                pass

        # 2) macOS Info.plist（若存在 .app）
        if sys.platform == "darwin":
            app_root = self._locate_app_bundle_root()
            if app_root:
                plist = app_root / "Contents" / "Info.plist"
                if plist.is_file():
                    try:
                        with plist.open("rb") as f:
                            info = plistlib.load(f)
                        meta.setdefault(
                            "display_name",
                            info.get("CFBundleDisplayName") or info.get("CFBundleName"),
                        )
                        meta.setdefault("name", info.get("CFBundleName"))
                        meta.setdefault(
                            "version",
                            info.get("CFBundleShortVersionString")
                            or info.get("CFBundleVersion"),
                        )
                        meta["_plist_path"] = str(plist)
                    except Exception:
                        pass

        return meta

    def _locate_app_bundle_root(self) -> Optional[Path]:
        exe = Path(sys.executable).resolve()
        for p in [exe] + list(exe.parents):
            if p.name.endswith(".app"):
                return p
        return None

    def _derive_app_name(self, meta: Dict) -> str:
        name = (meta.get("name") or meta.get("display_name") or "").strip()
        if not name and sys.platform == "darwin":
            app_root = self._locate_app_bundle_root()
            if app_root:
                name = app_root.stem
        if not name:
            name = (
                Path(sys.executable).stem
                if self._is_frozen()
                else self._detect_project_root(self._base_dir).name
            )
        return name.strip()

    def _canon_env_keys(self) -> List[str]:
        """
        根据应用名动态生成环境变量名（并兼容历史 XIAOZHI_*）。
        """
        canon = "".join(ch if ch.isalnum() else "_" for ch in self._app_name).upper()
        keys = [f"{canon}_DATA_DIR", f"{canon}_HOME", f"{canon}_RESOURCES_DIR"]
        # 历史兼容：如需完全移除旧名，删掉下一行即可
        keys += ["XIAOZHI_DATA_DIR", "XIAOZHI_HOME", "XIAOZHI_RESOURCES_DIR"]
        return keys

    def _build_search_dirs(self) -> List[Path]:
        dirs: List[Path] = []

        # 0) 环境变量（最高优先级）
        for key in self._canon_env_keys():
            v = os.getenv(key)
            if v:
                p = Path(v).resolve()
                if p.exists():
                    dirs.append(p)

        # 1) 运行基目录（_MEIPASS / exe_dir / project_root）
        dirs.append(self._base_dir)

        # 2) PyInstaller onedir v6: 可执行旁的 _internal
        try:
            exe_dir = Path(sys.executable).parent.resolve()
            internal = exe_dir / "_internal"
            if self._is_frozen() and internal.exists():
                dirs.append(internal)
        except Exception:
            pass

        # 3) 系统常见安装位置
        name = self._app_name
        if sys.platform == "darwin":
            candidates = [
                Path("/Library") / "Application Support" / name,
                Path("/usr/local/share") / name,
                Path("/opt") / name,
            ]
            app_root = self._locate_app_bundle_root()
            if app_root:
                res = app_root / "Contents" / "Resources"
                if res.exists():
                    candidates.insert(0, res)
            dirs += [c for c in candidates if c.exists()]
        elif sys.platform.startswith("linux"):
            dirs += [
                p
                for p in [
                    Path("/usr/share") / name,
                    Path("/usr/local/share") / name,
                    Path("/opt") / name,
                ]
                if p.exists()
            ]
        else:  # Windows
            dirs += [p for p in [Path("C:/ProgramData") / name] if p.exists()]

        # 4) 用户数据（可写/覆盖）
        dirs.append(self.get_user_data_dir(create=True))

        # 5) 最后兜底：cwd
        try:
            dirs.append(Path.cwd().resolve())
        except Exception:
            pass

        # 去重（保持顺序）
        out, seen = [], set()
        for d in dirs:
            d = d.resolve()
            if d not in seen:
                out.append(d)
                seen.add(d)
        return out

    def _find(self, relpath: PathLike, want_dir: bool) -> Optional[Path]:
        rp = Path(relpath)
        if rp.is_absolute():
            try:
                ok = rp.is_dir() if want_dir else rp.is_file()
            except OSError:
                ok = False
            return rp if ok else None

        for base in self._search_dirs:
            p = (base / rp).resolve()
            try:
                ok = p.is_dir() if want_dir else p.is_file()
            except OSError:
                ok = False
            if ok:
                return p
        return None


# --------- 可写目录迁移 / PATHS 覆盖 ---------


def _paths_overlap(src: Path, dst: Path) -> bool:
    """两个目录互相包含（或相同）时视为重叠，避免把目录拷进自身."""
    try:
        src = src.resolve()
        dst = dst.resolve()
    except Exception:
        return False
    if src == dst:
        return True
    try:
        return src.is_relative_to(dst) or dst.is_relative_to(src)
    except Exception:
        return False


def migrate_directory(
    src: Path,
    dst: Path,
    *,
    skip_dir_names: Optional[set] = None,
) -> Dict[str, Any]:
    """把旧目录内容复制到新目录（不删除源文件）.

    已存在的目标文件不覆盖，避免覆盖用户新目录中的内容。
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()
    skip = set(skip_dir_names or ())
    result: Dict[str, Any] = {
        "ok": False,
        "src": str(src),
        "dst": str(dst),
        "files_copied": 0,
    }

    if src == dst:
        result["ok"] = True
        result["skipped"] = "same_path"
        return result
    if _paths_overlap(src, dst):
        result["error"] = "源目录与目标目录重叠"
        return result
    if not src.is_dir():
        result["ok"] = True
        result["skipped"] = "src_missing"
        return result

    try:
        dst.mkdir(parents=True, exist_ok=True)
        count = 0
        for root, dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            if rel == Path("."):
                dirs[:] = [d for d in dirs if d not in skip]
            elif rel.parts and rel.parts[0] in skip:
                dirs[:] = []
                continue

            target_root = dst / rel
            target_root.mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = Path(root) / name
                target_file = target_root / name
                if target_file.exists():
                    continue
                try:
                    shutil.copy2(source_file, target_file)
                    count += 1
                except Exception as exc:
                    _logger.warning("迁移文件失败 %s: %s", source_file, exc)
        result["ok"] = True
        result["files_copied"] = count
    except Exception as exc:
        result["error"] = str(exc)
        _logger.warning("迁移目录失败 %s -> %s: %s", src, dst, exc)
    return result


def apply_path_overrides_from_config(
    config: Any,
    *,
    migrate: bool = True,
) -> List[Dict[str, Any]]:
    """读取 PATHS.* / MCP_PLUGINS.DIR，设置运行时覆盖并复制旧目录内容.

    环境变量始终优先于配置；config 目录本身不迁移。
    """
    global _override_cache, _override_log, _override_music
    global _override_keywords, _override_mcp_plugins

    results: List[Dict[str, Any]] = []

    def _cfg(key: str) -> Optional[str]:
        try:
            value = config.get_config(key, None)
        except Exception:
            return None
        if value is None or not str(value).strip():
            return None
        return str(value).strip()

    def _resolve(value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        try:
            return Path(value).expanduser().resolve()
        except Exception:
            return None

    data_dir = get_user_data_dir()

    # 覆盖前的有效路径，作为迁移源
    old_cache = get_user_cache_dir(create=False)
    old_log = get_user_log_dir(create=False)
    old_music = get_music_cache_dir(create=False)
    old_keywords = get_keywords_dir(create=False)

    cache_cfg = _resolve(_cfg("PATHS.CACHE_DIR"))
    log_cfg = _resolve(_cfg("PATHS.LOG_DIR"))
    music_cfg = _resolve(_cfg("PATHS.MUSIC_CACHE_DIR"))
    keywords_cfg = _resolve(_cfg("PATHS.KEYWORDS_DIR"))
    mcp_cfg = _resolve(_cfg("MCP_PLUGINS.DIR"))

    # 环境变量优先；音乐缓存默认跟随缓存目录
    proposed_cache = _env_path(ENV_CACHE_DIR) or cache_cfg or (data_dir / "cache")
    proposed_log = _env_path(ENV_LOG_DIR) or log_cfg or (data_dir / "logs")
    proposed_keywords = (
        _env_path(ENV_KEYWORDS_DIR) or keywords_cfg or (data_dir / "keywords")
    )
    proposed_music = (
        _env_path(ENV_MUSIC_CACHE_DIR) or music_cfg or (proposed_cache / "music")
    )
    proposed_mcp = mcp_cfg or (data_dir / "mcp_plugins")

    # 只应用配置值（环境变量在 getter 中已优先）
    _override_cache = cache_cfg
    _override_log = log_cfg
    _override_music = music_cfg
    _override_keywords = keywords_cfg
    _override_mcp_plugins = mcp_cfg

    if not migrate:
        return results

    migrations = (
        ("cache", old_cache, proposed_cache),
        ("logs", old_log, proposed_log),
        ("music", old_music, proposed_music),
        ("keywords", old_keywords, proposed_keywords),
    )

    for kind, old, new in migrations:
        old = Path(old)
        new = Path(new)
        if old == new:
            continue
        if _paths_overlap(old, new):
            result = {
                "kind": kind,
                "ok": False,
                "src": str(old),
                "dst": str(new),
                "files_copied": 0,
                "error": "源目录与目标目录重叠",
            }
            results.append(result)
            _logger.warning(
                "[PATHS] %s 目录迁移跳过：源与目标重叠 (%s -> %s)",
                kind,
                old,
                new,
            )
            continue

        skip_dirs: set = set()
        if kind == "cache":
            # 音乐缓存已单独指定到别处时，cache 迁移不要再拷旧 music 子树
            try:
                music_inside_old_cache = old_music == (old_cache / "music")
                music_moved_elsewhere = proposed_music != (proposed_cache / "music")
                if music_inside_old_cache and music_moved_elsewhere:
                    skip_dirs.add("music")
            except Exception:
                pass

        result = migrate_directory(old, new, skip_dir_names=skip_dirs)
        result["kind"] = kind
        results.append(result)
        if result.get("ok") and result.get("files_copied"):
            _logger.info(
                "[PATHS] %s 目录已迁移: %s -> %s（%s 个文件）",
                kind,
                old,
                new,
                result["files_copied"],
            )
        elif result.get("error"):
            _logger.warning("[PATHS] %s 目录迁移失败: %s", kind, result["error"])

    return results


# --------- 单例与便捷函数（含兼容） ---------
resource_finder = ResourceFinder()


def get_app_meta() -> Dict:
    return resource_finder.get_app_meta()


def get_app_name() -> str:
    return resource_finder.get_app_name()


def get_project_root() -> Path:
    return resource_finder.get_project_root()


def get_user_data_dir(create: bool = True) -> Path:
    return resource_finder.get_user_data_dir(create)


def get_user_cache_dir(create: bool = True) -> Path:
    return resource_finder.get_user_cache_dir(create)


def get_user_log_dir(create: bool = True) -> Path:
    return resource_finder.get_user_log_dir(create)


def get_log_dir(create: bool = True) -> Path:
    """日志目录（优先 PATHS.LOG_DIR / 环境变量）."""
    return resource_finder.get_user_log_dir(create)


def get_music_cache_dir(create: bool = True) -> Path:
    return resource_finder.get_music_cache_dir(create)


def get_keywords_dir(create: bool = True) -> Path:
    return resource_finder.get_keywords_dir(create)


def get_mcp_plugins_dir(create: bool = True) -> Path:
    return resource_finder.get_mcp_plugins_dir(create)


def get_default_music_dir() -> Path:
    """默认本地音乐目录：用户音乐目录下的 music 子目录。"""
    return Path.home() / "Music" / "music"


def find_file(p: PathLike) -> Optional[Path]:
    return resource_finder.find_file(p)


def find_directory(p: PathLike) -> Optional[Path]:
    return resource_finder.find_directory(p)


def find_models_dir() -> Optional[Path]:
    return resource_finder.find_models_dir()


def find_assets_dir() -> Optional[Path]:
    return resource_finder.find_assets_dir()


def find_config_dir() -> Optional[Path]:
    return resource_finder.find_config_dir()


# 兼容老签名：find_libs_dir(f"libopus/{system_dir}", arch_name) / find_libs_dir(system='Darwin', arch='arm64')
def find_libs_dir(
    *parts: PathLike, system: str = None, arch: str = None
) -> Optional[Path]:
    return resource_finder.find_libs_dir_compat(*parts, system=system, arch=arch)


# 可选：更细粒度查找
def find_models_subdir(*parts: PathLike) -> Optional[Path]:
    return resource_finder.find_under("models", *parts, want_dir=True)


def find_assets_subpath(*parts: PathLike) -> Optional[Path]:
    return resource_finder.find_under("assets", *parts, want_dir=False)
