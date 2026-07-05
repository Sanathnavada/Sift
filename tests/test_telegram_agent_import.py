import importlib


def test_telegram_agent_imports_without_optional_runtime_packages(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHATS", "111, 222")
    monkeypatch.setenv("TELEGRAM_POLL_INTERVAL", "3")

    import sift.engines.telegram.agent as agent

    agent = importlib.reload(agent)

    assert agent.ALLOWED_CHATS == ["111", "222"]
    assert agent.POLL_INTERVAL == 3
