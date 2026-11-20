"""5 號 v1 的模型：只有條款問答的模型帶 reasoning_effort（#462）。

守門（Jev）不用 chat model；取出欄位（extract）與更正（revise）共用預設模型，不帶這個參數。
用假的設定（azure_openai）建出來看模型的屬性，不呼叫 Azure。
"""

import pytest
from app.agents.incident import factory
from app.core.config import settings
from langchain_openai import AzureChatOpenAI


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch):
    """把 build_agent 交給各轉接函式的模型記下來。"""
    seen: dict[str, object] = {}
    for name in ("llm_extractor", "llm_reviser", "agent_answerer"):

        def record(model, _name=name):
            seen[_name] = model
            return lambda *a, **k: None

        monkeypatch.setattr(factory, name, record)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "azure_openai")
    monkeypatch.setattr(
        settings, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com"
    )
    monkeypatch.setattr(settings, "AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "AZURE_OPENAI_CHAT_DEPLOYMENT", "chat")
    monkeypatch.setattr(settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "embedding")
    return seen


def test_沒設定時_三個模型都不帶_reasoning_effort(models, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AZURE_OPENAI_QA_REASONING_EFFORT", "")

    factory.build_agent()

    for name in ("llm_extractor", "llm_reviser", "agent_answerer"):
        model = models[name]
        assert isinstance(model, AzureChatOpenAI)
        assert model.reasoning_effort is None


def test_設定_none_時_只有條款問答的模型帶_reasoning_effort(
    models, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "AZURE_OPENAI_QA_REASONING_EFFORT", "none")

    factory.build_agent()

    answer = models["agent_answerer"]
    assert isinstance(answer, AzureChatOpenAI)
    assert answer.reasoning_effort == "none"
    for name in ("llm_extractor", "llm_reviser"):
        other = models[name]
        assert isinstance(other, AzureChatOpenAI)
        assert other.reasoning_effort is None
