"""Deterministic provider route planning."""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.config_loader import config_loader
from ..utils.deterministic import canonical_json, stable_hash_hex


@dataclass
class RoutePlan:
    requested_model: str
    provider_name: str
    provider_model: str
    base_url: str
    upstream_path: str
    route_hash: str


_ENDPOINT_PATH = {
    "/v1/chat": "/chat/completions",
    "/v1/chat/completions": "/chat/completions",
    "/openai/v1/chat/completions": "/chat/completions",
    "/v1/responses": "/responses",
    "/openai/v1/responses": "/responses",
    "/v1/embeddings": "/embeddings",
    "/openai/v1/embeddings": "/embeddings",
}


def resolve_route(endpoint: str, model_name: str) -> tuple[RoutePlan | None, str | None]:
    model_config = config_loader.get_model(model_name)
    if not model_config:
        return None, f"Model '{model_name}' not allowed"

    provider = config_loader.get_provider(model_config.provider)
    if not provider:
        return None, f"Provider '{model_config.provider}' not configured"

    path = _ENDPOINT_PATH.get(endpoint)
    if not path:
        return None, f"Unsupported endpoint '{endpoint}'"

    route_payload = {
        "endpoint": endpoint,
        "provider": model_config.provider,
        "provider_model": model_config.provider_model,
        "requested_model": model_name,
        "base_url": provider.base_url,
    }

    return (
        RoutePlan(
            requested_model=model_name,
            provider_name=model_config.provider,
            provider_model=model_config.provider_model,
            base_url=provider.base_url,
            upstream_path=path,
            route_hash=stable_hash_hex(canonical_json(route_payload)),
        ),
        None,
    )
