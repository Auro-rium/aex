

import os
import sqlite3
from pathlib import Path


def get_base_url(port: int = 9000) -> str:
    """Get the AEX proxy base URL.
    
    Returns:
        str: e.g. 'http://127.0.0.1:9000/v1'
    """
    return f"http://127.0.0.1:{port}/v1"


def get_agent_token(agent_name: str) -> str:
    """Look up an agent's API token from the AEX database.
    
    Args:
        agent_name: Name of the agent to look up.
        
    Returns:
        str: The agent's API token.
        
    Raises:
        ValueError: If agent not found or database not available.
    """
    db_path = os.getenv("AEX_DB_PATH", str(Path.home() / ".aex" / "aex.db"))
    
    if not Path(db_path).exists():
        raise ValueError(
            f"AEX database not found at {db_path}. "
            "Run 'aex init' first or set AEX_DB_PATH."
        )
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT api_token FROM agents WHERE name = ?", (agent_name,)
        ).fetchone()
        if not row:
            raise ValueError(f"Agent '{agent_name}' not found in AEX database")
        return row["api_token"]
    finally:
        conn.close()


def get_openai_client(agent_name: str, port: int = 9000):
    """Create an OpenAI client configured to use AEX as the proxy.
    
    Requires the `openai` package to be installed.
    
    Args:
        agent_name: Name of the AEX agent.
        port: AEX daemon port (default: 9000).
        
    Returns:
        openai.OpenAI: Configured client instance.
        
    Raises:
        ImportError: If the `openai` package is not installed.
        ValueError: If the agent is not found.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "The 'openai' package is required for get_openai_client(). "
            "Install it with: pip install openai"
        )
    
    token = get_agent_token(agent_name)
    return OpenAI(
        base_url=get_base_url(port),
        api_key=token,
    )


def get_groq_client(agent_name: str, port: int = 9000):
    """Create a Groq client configured to use AEX as the proxy.
    
    Requires the `groq` package to be installed.
    
    Args:
        agent_name: Name of the AEX agent.
        port: AEX daemon port (default: 9000).
        
    Returns:
        groq.Client: Configured client instance.
        
    Raises:
        ImportError: If the `groq` package is not installed.
        ValueError: If the agent is not found.
    """
    try:
        from groq import Groq
    except ImportError:
        raise ImportError(
            "The 'groq' package is required for get_groq_client(). "
            "Install it with: pip install groq"
        )
    
    token = get_agent_token(agent_name)
    return Groq(
        base_url=get_base_url(port),
        api_key=token,
    )


def configure_environment(agent_name: str, port: int = 9000) -> dict[str, str]:
    """Set environment variables for framework integration.
    
    Sets OPENAI_BASE_URL and OPENAI_API_KEY so that any framework
    using the OpenAI SDK will route through AEX automatically.
    
    Args:
        agent_name: Name of the AEX agent.
        port: AEX daemon port (default: 9000).
        
    Returns:
        dict: The environment variables that were set.
    """
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
