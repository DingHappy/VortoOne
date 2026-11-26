"""Shared opt-in unit test fixtures."""
import os
import shlex
import shutil

import pytest


@pytest.fixture
def isolated_personal_codex_git(tmp_path_factory, monkeypatch):
    """Keep the host's Git configuration out of temporary-repository tests."""
    git = shutil.which("git")
    assert git is not None
    directory = tmp_path_factory.mktemp("personal-codex-git")
    global_config = directory / "gitconfig"
    global_config.touch()
    wrapper = directory / "git"
    # personal_git strips inherited GIT_* variables, so set these inside the wrapper.
    wrapper.write_text(
        "#!/bin/sh\n"
        "unset GIT_CONFIG GIT_CONFIG_PARAMETERS\n"
        f"export GIT_CONFIG_GLOBAL={shlex.quote(str(global_config))}\n"
        "export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=0\n"
        f'exec {shlex.quote(git)} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", str(directory), prepend=os.pathsep)
    return global_config


@pytest.fixture
def mcp_permission_probe(tmp_path, monkeypatch):
    import yaml
    import src.tools.manager as module
    from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport
    from src.agents.mcp_tools import wrap_mcp_manager

    class Client:
        server_name = 'probe'
        transport = MCPTransport.HTTP
        connected = False
        calls = 0
        async def connect(self): self.connected = True; return True
        async def disconnect(self): self.connected = False
        async def list_tools(self): return [MCPTool(name='act', server_name='probe')]
        async def call_tool(self, call): self.calls += 1; return MCPToolResult(success=True, output='probe executed')
    client = Client()
    monkeypatch.setattr(module, 'create_mcp_client', lambda *a, **k: client)
    async def connect(confirm=None, action='allow'):
        path = tmp_path / 'mcp-probe.yaml'
        path.write_text(yaml.safe_dump({'servers': [{'name': 'probe', 'transport': 'http', 'url': 'https://fixture.example.test/mcp'}],
            'permissions': {'rules': [{'id': 'probe', 'action': action, 'tool_names': ['act'], 'priority': 999}]}}))
        manager = module.ToolManager(str(path), confirm=confirm)
        assert await manager.initialize()
        return manager, wrap_mcp_manager(manager), client
    return connect


@pytest.fixture
def readonly_connector_probe(tmp_path, monkeypatch):
    """Real config -> connector selection -> manager -> executor, fake transport only."""
    import yaml
    import src.tools.manager as module
    from src.agents import mcp_tools
    from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport

    monkeypatch.setattr(mcp_tools, 'connector_secret_path', lambda _: tmp_path / 'probe.token')
    secret = tmp_path / 'probe.token'
    secret.write_text('fixture-token')
    secret.chmod(0o600)
    clients = {}

    class Client:
        transport = MCPTransport.HTTP
        connected = False
        def __init__(self, server):
            self.server_name, self.calls = server, []
        async def connect(self): self.connected = True; return True
        async def disconnect(self): self.connected = False
        async def list_tools(self):
            return [MCPTool(name='read', server_name=self.server_name, annotations={'readOnlyHint': True}),
                    MCPTool(name='write', server_name=self.server_name)]
        async def call_tool(self, call):
            self.calls.append(call.tool_name)
            return MCPToolResult(success=True, output='probe executed')

    def factory(server, *args, **kwargs):
        clients[server] = Client(server)
        return clients[server]
    monkeypatch.setattr(module, 'create_mcp_client', factory)

    async def connect(confirm=None, action='allow', *, profile='external', allow_connectors=True):
        (tmp_path / 'config').mkdir(exist_ok=True)
        (tmp_path / 'config/mcp.yaml').write_text(yaml.safe_dump({
            'servers': [
                {'name': 'trusted', 'transport': 'http', 'url': 'https://fixture.example.test/mcp',
                 'credential': 'probe', 'read_only': True},
                {'name': 'plain', 'transport': 'http', 'url': 'https://fixture.example.test/mcp',
                 'credentialed': False}],
            'permissions': {'rules': [{'id': 'probe', 'action': action,
                                       'tool_names': ['read', 'write'], 'priority': 999}]}}))
        manager, tools = await mcp_tools.connect_mcp(tmp_path, capability_profile=profile,
                                                    confirm=confirm, allow_connectors=allow_connectors)
        return manager, tools, clients
    return connect
