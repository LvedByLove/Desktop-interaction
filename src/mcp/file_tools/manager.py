"""文件工具管理器：注册受控文件操作 MCP 工具."""

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class FileToolsManager:
    """注册文件新建/修改/读取/列目录工具."""

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        from .tools import (
            append_file,
            create_directory,
            create_or_write,
            list_directory,
            read_file,
        )

        add_tool(
            (
                "file.create_or_write",
                "在允许目录内创建或覆盖一个文本文件。"
                "会自动创建父目录；目标文件已存在时必须显式传 overwrite=true。"
                "覆盖前默认会生成 .bak 备份。"
                "参数：path（相对根目录或绝对路径）、content（文本内容）、"
                "overwrite（布尔值，默认 false）。",
                PropertyList(
                    [
                        Property("path", PropertyType.STRING),
                        Property(
                            "content", PropertyType.STRING, default_value=""
                        ),
                        Property(
                            "overwrite", PropertyType.BOOLEAN, default_value=False
                        ),
                    ]
                ),
                create_or_write,
            )
        )

        add_tool(
            (
                "file.append",
                "向允许目录内的文本文件末尾追加内容；文件不存在时会自动创建。"
                "参数：path、content。",
                PropertyList(
                    [
                        Property("path", PropertyType.STRING),
                        Property("content", PropertyType.STRING),
                    ]
                ),
                append_file,
            )
        )

        add_tool(
            (
                "file.read",
                "读取允许目录内的文本文件内容。"
                "用于修改文件前确认现有内容。参数：path。",
                PropertyList([Property("path", PropertyType.STRING)]),
                read_file,
            )
        )

        add_tool(
            (
                "file.list_dir",
                "列出允许目录内的文件和文件夹。"
                "参数：path（留空表示根目录）、limit（最多返回项数，默认 200）。",
                PropertyList(
                    [
                        Property(
                            "path", PropertyType.STRING, default_value=""
                        ),
                        Property(
                            "limit",
                            PropertyType.INTEGER,
                            default_value=200,
                            min_value=1,
                            max_value=1000,
                        ),
                    ]
                ),
                list_directory,
            )
        )

        add_tool(
            (
                "file.create_dir",
                "在允许目录内创建文件夹（含不存在的父目录）。参数：path。",
                PropertyList([Property("path", PropertyType.STRING)]),
                create_directory,
            )
        )

        logger.info("文件操作 MCP 工具注册完成")


_file_tools_manager = None


def get_file_tools_manager() -> FileToolsManager:
    global _file_tools_manager
    if _file_tools_manager is None:
        _file_tools_manager = FileToolsManager()
    return _file_tools_manager
