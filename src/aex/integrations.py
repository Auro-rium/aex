"""Framework integration helpers for AEX."""

from __future__ import annotations

import os

from .daemon.db import get_db_connection


def get_base_url(port: int = 9000) -> str:
    """Get the AEX proxy base URL."""
    return f"http://127.0.0.1:{port}/v1"


def get_agent_token(agent_name: str) -> str:
    """Look up an agent's API token from the configured PostgreSQL database."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT api_token FROM agents WHERE name = ?",
                (agent_name,),
            ).fetchone()
    except Exception as exc:
        raise ValueError(
            "AEX database is not reachable. Set AEX_PG_DSN and initialize via /admin/console or startup init_db()."
        ) from exc

    if not row:
        raise ValueError(f"Agent '{agent_name}' not found in AEX database")
    return row["api_token"]


def get_openai_client(agent_name: str, port: int = 9000):
    """Create an OpenAI client configured to use AEX as the proxy."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError(
            "The 'openai' package is required for get_openai_client(). "
            "Install it with: pip install openai"
        ) from exc

    token = get_agent_token(agent_name)
    return OpenAI(base_url=get_base_url(port), api_key=token)


def get_groq_client(agent_name: str, port: int = 9000):
    """Create a Groq client configured to use AEX as the proxy."""
    try:
        from groq import Groq
    except ImportError as exc:
        raise ImportError(
            "The 'groq' package is required for get_groq_client(). "
            "Install it with: pip install groq"
        ) from exc

    token = get_agent_token(agent_name)
    return Groq(base_url=get_base_url(port), api_key=token)


def configure_environment(agent_name: str, port: int = 9000) -> dict[str, str]:
    """Set environment variables for framework integration."""
    token = get_agent_token(agent_name)
    base_url = get_base_url(port)

    env_vars = {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": token,
        "AEX_AGENT_TOKEN": token,
    }

    for key, value in env_vars.items():
        os.environ[key] = value

    return env_vars
