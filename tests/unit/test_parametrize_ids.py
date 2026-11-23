"""超大的参数不当测试名：CI 会逐条打印 nodeid，几十万字符一行能把整个 job 卡到超时（见 tests/conftest.py）。"""
import pytest


@pytest.mark.parametrize("value", ["x" * 262_145, b"\xff" * 4096, "短"])
def test_an_oversized_input_is_not_the_test_name(request, value):
    assert len(request.node.nodeid) < 250
