import yaml
import os
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import Dict, Optional, Literal
from .logging_config import StructuredLogger

logger = StructuredLogger(__name__)

# --- V1 Schema Models ---

class ModelCapabilities(BaseModel):
    reasoning: bool = False
    tools: bool = False
    vision: bool = False

class ModelPricing(BaseModel):
    input_micro: int = Field(..., ge=0)
    output_micro: int = Field(..., ge=0)

class ModelLimits(BaseModel):
    max_tokens: int = Field(..., ge=1)

class ModelConfig(BaseModel):
    provider: str
    provider_model: str
    pricing: ModelPricing
    limits: ModelLimits
    capabilities: ModelCapabilities

class ProviderConfig(BaseModel):
    base_url: str
    type: Literal["openai_compatible"]

class AEXConfig(BaseModel):
    version: int = Field(1, ge=1, le=1)
    providers: Dict[str, ProviderConfig]
    models: Dict[str, ModelConfig]
    default_model: Optional[str] = None

    @field_validator("default_model")
    def validate_default_model(cls, v, values):
        if v and "models" in values.data and v not in values.data["models"]:
            raise ValueError(f"Default model '{v}' not found in models list")
        return v

# --- Config Loader (Atomic Reload) ---

class ConfigLoader:
    def __init__(self):
        self.config_dir = Path(os.getenv("AEX_CONFIG_DIR", "/etc/aex/config"))
        self.config_file = self.config_dir / "models.yaml"
        self.config: Optional[AEXConfig] = None

    def load_config(self) -> AEXConfig:
        """
        Loads and validates configuration from models.yaml.
        ATOMIC: On failure, previous config is preserved.
        Raises ValueError if invalid and no previous config exists.
        """
        if not self.config_file.exists():
            logger.critical("Config file not found", path=str(self.config_file))
            raise FileNotFoundError(f"Config file not found at {self.config_file}")

        try:
            with open(self.config_file, "r") as f:
                raw_data = yaml.safe_load(f)
            
            logger.info("Loading configuration", path=str(self.config_file))

            # Validate into temporary — never touch self.config until success
            new_config = AEXConfig(**raw_data)
            
            # Validation passed — atomic swap
            self.config = new_config
            
            logger.info("Configuration loaded successfully", 
                        version=self.config.version, 
                        models=list(self.config.models.keys()))
            return self.config

        except Exception as e:
            logger.error("Configuration validation failed", error=str(e))
            if self.config is not None:
                logger.warning("Keeping previous valid configuration")
                raise ValueError(f"Invalid configuration (previous config retained): {e}")
            else:
                logger.critical("No previous configuration to fall back to")
                raise ValueError(f"Invalid configuration (no fallback): {e}")

    def get_model(self, model_name: str) -> Optional[ModelConfig]:
        if not self.config:
            self.load_config()
        return self.config.models.get(model_name)

    def get_provider(self, provider_name: str) -> Optional[ProviderConfig]:
        if not self.config:
            self.load_config()
        return self.config.providers.get(provider_name)

    def get_default_model(self) -> str:
        if not self.config:
            self.load_config()
        if self.config.default_model:
            return self.config.default_model
        return next(iter(self.config.models.keys()))

config_loader = ConfigLoader()
