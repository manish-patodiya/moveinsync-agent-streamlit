from __future__ import annotations

import os
from typing import Protocol

from dotenv import load_dotenv

DEFAULT_OLLAMA_MODEL = "qwen:14b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_LLM_PROVIDER = "groq"


class LLMProvider(Protocol):
    def invoke(self, input: object) -> object: ...


def describe_llm() -> dict[str, str | bool]:
    """Report the configured provider so the UI can show what Reason will use."""
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()
    if provider == "ollama":
        return {
            "provider": "ollama",
            "model": os.getenv("LLM_MODEL", DEFAULT_OLLAMA_MODEL),
            "available": True,
        }
    if provider == "groq":
        return {
            "provider": "groq",
            "model": os.getenv("LLM_MODEL", DEFAULT_GROQ_MODEL),
            "available": bool(os.getenv("GROQ_API_KEY")),
        }
    if provider == "openai":
        return {
            "provider": "openai",
            "model": os.getenv("LLM_MODEL", ""),
            "available": bool(os.getenv("OPENAI_API_KEY") and os.getenv("LLM_MODEL")),
        }
    return {"provider": provider, "model": "", "available": False}


def get_llm() -> LLMProvider | None:
    """The only LLM construction point. Configuration comes from environment only."""
    config = describe_llm()
    if not config["available"]:
        return None
    if config["provider"] == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=str(config["model"]),
            base_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            temperature=0,
        )
    if config["provider"] == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=str(config["model"]), temperature=0)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=str(config["model"]), temperature=0)
