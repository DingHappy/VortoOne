"""运行中会变的模型端点：地址和 key 永远成对读写，停用的端点不再被用。

所有者在新版界面里加、换、停端点时（``web/app_credentials.apply``）改的是进程环境。客户端可能在任何线程、
任何时刻创建，也可能早就创建好了，所以：

- 改和读都在 ``LOCK`` 里：不会读到「新地址 + 旧 key」；
- 登记过的（地址, key）被换掉或停用时记一笔：还拿着旧的那对的客户端（含它的副本）换 key 就跟上，
  停用就报错停下，绝不改用默认端点——那等于把内容发给了没被选中的一家；
- 默认端点一变，代次加一：用默认端点的客户端在下一次请求前跟着换。
"""
from __future__ import annotations

import re
import threading
from typing import Dict, Iterable, Optional, Tuple

Pair = Tuple[str, str]

LOCK = threading.RLock()
_CUSTOM = re.compile(r"\s*custom:\s*cred-[0-9a-f]{12}\s*:", re.IGNORECASE)
_replaced: Dict[Pair, Optional[Pair]] = {}
_generation = 0
UNAVAILABLE = "这个模型端点已停用或不存在，请求没有发出，也不会改发给别的端点；请给它换一个模型（重新添加过的密钥也要重新选一次）"


class EndpointUnavailable(ValueError):
    """点名的端点不能用了：请求停在本机。"""


def is_endpoint_model(model: object) -> bool:
    """是不是 ``custom:<密钥 id>:<模型>``。这种写法只在本机解析，永远不该原样发给任何一家。"""
    return isinstance(model, str) and _CUSTOM.match(model) is not None


def generation() -> int:
    return _generation


def default_changed() -> None:
    """调用方须持有 LOCK。"""
    global _generation
    _generation += 1


def replaced(changes: Iterable[Tuple[Pair, Optional[Pair]]], current: Iterable[Pair]) -> None:
    """登记被换掉（新的一对）或停用（None）的端点；现在还有效的永远不算被换掉。调用方须持有 LOCK。"""
    _replaced.update(changes)
    for pair in current:
        _replaced.pop(pair, None)


def resolve(pair: Pair) -> Pair:
    """这一对现在该用哪一对；停用了就抛 EndpointUnavailable。"""
    with LOCK:
        while pair in _replaced:
            following = _replaced[pair]
            if following is None:
                raise EndpointUnavailable(UNAVAILABLE)
            pair = following
        return pair
