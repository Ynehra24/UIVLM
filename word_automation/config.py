from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Automatically load .env if available
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()


@dataclass
class AutomationConfig:
    """Configuration for Word automation pipeline."""

    # API keys and endpoints
    nemotron_api_key: str = field(
        default_factory=lambda: os.environ.get("NV_KEY") or os.environ.get("NEMOTRON_API_KEY", "")
    )
    nemotron_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "NEMOTRON_BASE_URL",
            os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        )
    )
    osaurus_base_url: str = field(
        default_factory=lambda: os.environ.get("OSAURUS_BASE_URL", "http://localhost:8080/v1")
    )

    # Model identifiers
    nemotron_model: str = field(
        default_factory=lambda: os.environ.get(
            "NV_PRIMARY_MODEL",
            os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
        )
    )
    osaurus_model: str = field(
        default_factory=lambda: os.environ.get("OSAURUS_MODEL", "osaurus-applescript-8b")
    )

    # Timeouts (seconds)
    applescript_timeout: int = 10
    sdef_extraction_timeout: int = 5

    # Caching
    cache_sdef: bool = True
    cache_dir: str = ".automation_cache"

    @classmethod
    def from_env(cls, **overrides) -> AutomationConfig:
        """Create config populated from environment variables with optional overrides."""
        config = cls()
        for k, v in overrides.items():
            if hasattr(config, k) and v is not None:
                setattr(config, k, v)
        return config
