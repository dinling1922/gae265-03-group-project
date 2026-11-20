from pathlib import Path

import pytest
from app.core.config import Settings, settings
from app.core.llm import get_chat_model, get_embeddings
from app.main import app
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)


async def test_tests_always_get_fake_models() -> None:
    reply = await get_chat_model().ainvoke("hi")
    assert reply.content == "fake response"
    vectors = await get_embeddings().aembed_documents(["a", "b"])
    assert len(vectors) == 2


def test_env_var_overrides_cloud_provider_in_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("LLM_PROVIDER=azure_openai\n")
    assert Settings(_env_file=dotenv).LLM_PROVIDER == "fake"  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    ("config", "chat_cls", "embeddings_cls"),
    [
        (
            {
                "LLM_PROVIDER": "azure_openai",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
                "AZURE_OPENAI_API_KEY": "test-key",
                "AZURE_OPENAI_CHAT_DEPLOYMENT": "chat",
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "embedding",
            },
            AzureChatOpenAI,
            AzureOpenAIEmbeddings,
        ),
        (
            {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
            ChatOpenAI,
            OpenAIEmbeddings,
        ),
        ({"LLM_PROVIDER": "ollama"}, ChatOllama, OllamaEmbeddings),
    ],
)
def test_provider_selects_models(
    config: dict[str, str], chat_cls: type, embeddings_cls: type
) -> None:
    s = Settings(**config)  # pyright: ignore[reportArgumentType]
    assert isinstance(get_chat_model(s), chat_cls)
    assert isinstance(get_embeddings(s), embeddings_cls)


@pytest.mark.parametrize(
    ("provider", "missing"),
    [
        ("azure_openai", "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
    ],
)
def test_missing_provider_settings_fail_clearly(
    monkeypatch: pytest.MonkeyPatch, provider: str, missing: str
) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # 載入設定不需要 LLM 設定（alembic、匯出 OpenAPI 只用到資料庫設定）
    s = Settings(_env_file=None, LLM_PROVIDER=provider)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match=missing):
        s.check_llm_provider()
    with pytest.raises(ValueError, match=missing):
        get_chat_model(s)


async def test_app_startup_fails_clearly_without_llm_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        async with app.router.lifespan_context(app):
            pass


def test_fake_provider_is_rejected_in_production() -> None:
    s = Settings(_env_file=None, ENVIRONMENT="production", LLM_PROVIDER="fake")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match="production"):
        s.check_llm_provider()


def test_cloud_clients_have_a_request_timeout_and_bounded_retries() -> None:
    """沒設逾時的話，一次卡住的連線會讓 API、匯入、評估無限等下去。"""
    azure = Settings(
        LLM_PROVIDER="azure_openai",
        AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com",
        AZURE_OPENAI_API_KEY="test-key",  # pyright: ignore[reportArgumentType]
        AZURE_OPENAI_CHAT_DEPLOYMENT="chat",
        AZURE_OPENAI_EMBEDDING_DEPLOYMENT="embedding",
    )
    openai = Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test-key")  # pyright: ignore[reportArgumentType]
    for client in (
        get_chat_model(azure),
        get_embeddings(azure),
        get_chat_model(openai),
        get_embeddings(openai),
    ):
        assert client.request_timeout == 30  # pyright: ignore[reportAttributeAccessIssue]
        assert client.max_retries == 3  # pyright: ignore[reportAttributeAccessIssue]


# ---- 條款問答專用的模型（#462）：luna 等推理模型帶工具要 reasoning_effort="none"，
# gpt-4.1-mini 等非推理模型不認得這個參數（400），所以只在設定有值時、只給條款問答用 ----

AZURE = {
    "LLM_PROVIDER": "azure_openai",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "chat",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "embedding",
}


def request_payload(model) -> dict:
    """模型送出請求時的參數（不呼叫 Azure）。"""
    from langchain_core.messages import HumanMessage

    return model._get_request_payload([HumanMessage("hi")])


def test_qa_model_without_setting_is_the_same_as_the_default_model() -> None:
    from app.core.llm import get_qa_chat_model

    s = Settings(**AZURE)  # pyright: ignore[reportArgumentType]
    assert s.AZURE_OPENAI_QA_REASONING_EFFORT == ""

    qa = get_qa_chat_model(s)

    assert isinstance(qa, AzureChatOpenAI)
    assert qa.reasoning_effort is None
    assert "reasoning_effort" not in request_payload(qa)


def test_qa_model_with_setting_carries_reasoning_effort_but_the_default_model_does_not() -> (
    None
):
    from app.core.llm import get_qa_chat_model

    s = Settings(**AZURE, AZURE_OPENAI_QA_REASONING_EFFORT="none")  # pyright: ignore[reportArgumentType]

    qa = get_qa_chat_model(s)
    default = get_chat_model(s)

    assert isinstance(qa, AzureChatOpenAI)
    assert qa.reasoning_effort == "none"
    assert request_payload(qa)["reasoning_effort"] == "none"
    # get_chat_model() 的行為不變：其他用 LLM 的地方都不帶
    assert isinstance(default, AzureChatOpenAI)
    assert default.reasoning_effort is None
    assert "reasoning_effort" not in request_payload(default)


@pytest.mark.parametrize(
    ("config", "chat_cls"),
    [
        ({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}, ChatOpenAI),
        ({"LLM_PROVIDER": "ollama"}, ChatOllama),
    ],
)
def test_qa_setting_only_applies_to_azure_openai(
    config: dict[str, str], chat_cls: type
) -> None:
    from app.core.llm import get_qa_chat_model

    s = Settings(**config, AZURE_OPENAI_QA_REASONING_EFFORT="none")  # pyright: ignore[reportArgumentType]

    qa = get_qa_chat_model(s)

    assert isinstance(qa, chat_cls)
    assert getattr(qa, "reasoning_effort", None) is None


def test_qa_model_for_the_fake_provider_is_unchanged() -> None:
    from app.core.llm import get_qa_chat_model

    s = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        LLM_PROVIDER="fake",
        AZURE_OPENAI_QA_REASONING_EFFORT="none",
    )

    assert type(get_qa_chat_model(s)) is type(get_chat_model(s))
