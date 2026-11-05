"""站点适配器注册表。

新增站点只需要在本目录放一个模块，里面定义 ``SiteAdapter`` 的子类并设置
``name`` / ``hosts`` / ``PROBE_JS``，注册表会在首次使用时自动发现。
"""

from __future__ import annotations

import importlib
import pkgutil

from .base import SiteAdapter

__all__ = ["SiteAdapter", "SITE_ADAPTERS", "get_adapter_for", "reload_adapters"]


def _discover() -> list:
    adapters = []
    package = __name__
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            module = importlib.import_module("%s.%s" % (package, info.name))
        except Exception:
            continue
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, SiteAdapter)
                and value is not SiteAdapter
                and getattr(value, "name", "base") != "base"
            ):
                try:
                    adapters.append(value())
                except Exception:
                    continue
    return adapters


SITE_ADAPTERS = _discover()


def reload_adapters() -> list:
    global SITE_ADAPTERS
    SITE_ADAPTERS = _discover()
    return SITE_ADAPTERS


def get_adapter_for(url: str):
    for adapter in SITE_ADAPTERS:
        try:
            if adapter.matches(url):
                return adapter
        except Exception:
            continue
    return None
