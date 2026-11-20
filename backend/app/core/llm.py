"""取得 chat model 與 embeddings 的單一入口；呼叫端只依賴 LangChain 抽象介面。"""

from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.core.config import Settings, settings

# 雲端供應商的呼叫逾時（秒）與重試次數；沒設逾時時，一次卡住的連線會讓呼叫端無限等下去
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def get_chat_model(s: Settings = settings) -> BaseChatModel:
    s.check_llm_provider()
    match s.LLM_PROVIDER:
        case "azure_openai":
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
                api_key=s.AZURE_OPENAI_API_KEY,
                api_version=s.AZURE_OPENAI_API_VERSION,
                azure_deployment=s.AZURE_OPENAI_CHAT_DEPLOYMENT,
                timeout=REQUEST_TIMEOUT,
                max_retries=MAX_RETRIES,
            )
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                api_key=s.OPENAI_API_KEY,
                model=s.OPENAI_CHAT_MODEL,
                timeout=REQUEST_TIMEOUT,
                max_retries=MAX_RETRIES,
            )
        case "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(base_url=s.OLLAMA_BASE_URL, model=s.OLLAMA_CHAT_MODEL)
        case "fake":
            return FakeListChatModel(responses=["fake response"])


def get_qa_chat_model(s: Settings = settings) -> BaseChatModel:
    """5 號條款問答用的 chat model（#462）：問答要帶工具，gpt-6-luna 這類推理模型要
    reasoning_effort="none" 才不會回 400；gpt-4.1-mini 不認得這個參數，所以預設不帶。

    只在 azure_openai 且 INCIDENT_QA_REASONING_EFFORT 有值時才帶；其他供應商、沒設定時
    和 get_chat_model() 一樣。get_chat_model() 本身不變，其他用 LLM 的地方不受影響。"""
    model = get_chat_model(s)
    effort = s.INCIDENT_QA_REASONING_EFFORT
    if s.LLM_PROVIDER == "azure_openai" and effort:
        return model.model_copy(update={"reasoning_effort": effort})
    return model


def get_embeddings(s: Settings = settings) -> Embeddings:
    s.check_llm_provider()
    match s.LLM_PROVIDER:
        case "azure_openai":
            from langchain_openai import AzureOpenAIEmbeddings

            return AzureOpenAIEmbeddings(
                azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
                api_key=s.AZURE_OPENAI_API_KEY,
                api_version=s.AZURE_OPENAI_API_VERSION,
                azure_deployment=s.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                timeout=REQUEST_TIMEOUT,
                max_retries=MAX_RETRIES,
                dimensions=s.EMBEDDING_DIMENSIONS,
            )
        case "openai":
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(
                api_key=s.OPENAI_API_KEY,
                model=s.OPENAI_EMBEDDING_MODEL,
                dimensions=s.EMBEDDING_DIMENSIONS,
                timeout=REQUEST_TIMEOUT,
                max_retries=MAX_RETRIES,
            )
        case "ollama":
            from langchain_ollama import OllamaEmbeddings

            return OllamaEmbeddings(
                base_url=s.OLLAMA_BASE_URL, model=s.OLLAMA_EMBEDDING_MODEL
            )
        case "fake":
            return DeterministicFakeEmbedding(size=s.EMBEDDING_DIMENSIONS)


def chat_model_name(s: Settings = settings) -> str:
    """目前聊天模型的名稱，連同供應商記在結果裡：不同供應商與模型的結果不能直接比較。"""
    s.check_llm_provider()
    match s.LLM_PROVIDER:
        case "azure_openai":
            return s.AZURE_OPENAI_CHAT_DEPLOYMENT or ""
        case "openai":
            return s.OPENAI_CHAT_MODEL
        case "ollama":
            return s.OLLAMA_CHAT_MODEL
        case "fake":
            return "fake"


def embedding_model_name(s: Settings = settings) -> str:
    """向量資料表的 `model` 欄位值：只搜尋目前設定的 model 產生的向量。"""
    s.check_llm_provider()
    match s.LLM_PROVIDER:
        case "azure_openai":
            return s.AZURE_OPENAI_EMBEDDING_DEPLOYMENT or ""
        case "openai":
            return s.OPENAI_EMBEDDING_MODEL
        case "ollama":
            return s.OLLAMA_EMBEDDING_MODEL
        case "fake":
            return "fake"
