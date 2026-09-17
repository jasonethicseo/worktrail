"""Authentication failures stay visible without killing the stdio proxy."""

import anyio
import pytest

from casebook.adapters import mcp_proxy


def test_corrupt_login_file_says_to_login_again(tmp_path, monkeypatch):
    token = tmp_path / "oauth.json"
    token.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("CASEBOOK_OAUTH_FILE", str(token))

    with pytest.raises(mcp_proxy.LoginRequired, match="casebook-login"):
        mcp_proxy._oauth_ready()


def test_initial_auth_rejection_keeps_proxy_alive():
    async def run():
        proxy = mcp_proxy.Proxy("https://example.test/mcp")

        async def rejected():
            raise mcp_proxy.LoginRequired("인증이 만료됐다. casebook-login 으로 다시 로그인한다.")

        proxy.connect = rejected
        async with anyio.create_task_group() as tg:
            await tg.start(proxy.own)
            assert proxy.session is None
            assert [tool.name for tool in proxy.host_tools()] == ["connection_status"]
            result = await proxy.call("connection_status", {})
            assert result.is_error
            assert "casebook-login" in result.content[0].text
            tg.cancel_scope.cancel()

    anyio.run(run)
