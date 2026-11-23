"""Keyless model calls pin the TCP address and preserve the configured TLS identity."""

import socket
import ssl
from datetime import datetime, timedelta, timezone
from functools import partial

import aiohttp
from aiohttp import web
import openai
import pytest

from src.llm import client as llm


@pytest.fixture
def tls_localhost(tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "localhost.pem"
    key_file = tmp_path / "localhost.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_file, key_file)
    trusted = ssl.create_default_context(cafile=cert_file)
    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", partial(openai.DefaultAsyncHttpxClient, verify=trusted))
    original_session = llm.local_session

    def trusted_local_session(**options):
        return original_session(connector=aiohttp.TCPConnector(ssl=trusted), **options)

    monkeypatch.setattr(llm, "local_session", trusted_local_session)
    return server


async def test_keyless_localhost_sends_sdk_and_aiohttp_requests_without_resolving_alias(tls_localhost, monkeypatch):
    requests = []
    lookups = []
    original_getaddrinfo = socket.getaddrinfo

    def refuse_localhost_lookup(host, *args, **kwargs):
        if host in ("localhost", b"localhost"):
            lookups.append(host)
            raise OSError("localhost resolution disabled in test")
        return original_getaddrinfo(host, *args, **kwargs)

    async def chat(request):
        requests.append((request.method, request.host))
        payload = await request.json()
        if payload["messages"][0]["content"] == "redirect":
            raise web.HTTPTemporaryRedirect("/unexpected")
        return web.json_response({
            "id": "local-test",
            "object": "chat.completion",
            "created": 1,
            "model": "offline-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "local reply"}}],
        })

    async def models(request):
        requests.append((request.method, request.host))
        return web.json_response({"data": [{"id": "offline-model"}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls_localhost)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(socket, "getaddrinfo", refuse_localhost_lookup)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_BASE", f"https://localhost:{port}/v1")
    monkeypatch.setenv("DEFAULT_MODEL", "offline-model")
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    model = llm.LLMClient()
    try:
        assert model.config.base_url == f"https://localhost:{port}/v1"
        greeting = [{"role": "user", "content": "hello"}]
        assert (await model.chat(greeting))["content"] == "local reply"
        assert (await model._chat_with_requests(greeting))["content"] == "local reply"
        assert await model.list_models() == ["offline-model"]
        redirect = [{"role": "user", "content": "redirect"}]
        with pytest.raises(openai.APIStatusError) as failed:
            await model.chat(redirect)
        assert failed.value.status_code == 307
        with pytest.raises(Exception, match="307"):
            await model._chat_with_requests(redirect)
        # The same trusted leaf does not cover a configured numeric address. Both transports must check its name.
        monkeypatch.setenv("OPENAI_API_BASE", f"https://127.0.0.1:{port}/v1")
        wrong_name = llm.LLMClient()
        wrong_name.config.max_retries = 0
        try:
            with pytest.raises(openai.APIConnectionError):
                await wrong_name.chat(greeting)
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await wrong_name.list_models()
        finally:
            await wrong_name.close()
        assert lookups == []
        assert requests == [(method, f"localhost:{port}") for method in ("POST", "POST", "GET", "POST", "POST")]
    finally:
        await model.close()
        await runner.cleanup()
