from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Try loading .env from parent directory or current directory
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv()

API_KEY = os.environ.get("OPENROUTER_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
main_model = os.environ.get("OPENROUTER_MODELS")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SECONDS = int(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "120"))

LOG_FILE = os.environ.get("SCRIPTAGENT_LOG", "scriptagent.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
LOGGER = logging.getLogger("scriptagent")

SLOW_OPERATION_SECONDS = float(os.environ.get("SLOW_OPERATION_SECONDS", "3.0"))

sdef_file = "/Applications/Microsoft Word.app/Contents/Resources/Word.sdef"
WORD_SDEF_PATHS = [
    sdef_file,
    "/Applications/Microsoft Word.app/Contents/Resources/Word.sdef",
    "/Applications/Microsoft Word.app/Contents/Resources/Word 2019.sdef",
]

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "scriptagent"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SDEF_DB_PATH = str(CACHE_DIR / "sdef_index.db")
SDEF_GRAPH_DB_PATH = str(CACHE_DIR / "sdef_graph.json")
LOG_PATH = Path(LOG_FILE)


def get_openrouter_key() -> str:
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")
    return API_KEY


# Verified available via https://openrouter.ai/api/v1/models (pricing.prompt == "0")
DEFAULT_FAST_MODELS = [
    "google/gemma-4-31b-it:free",               # 31B — fast, good code gen
    "nvidia/nemotron-3-super-120b-a12b:free",   # 120B — strongest but slow
    "openrouter/free",                           # last-resort auto-router
]

def get_openrouter_models() -> list[str]:
    if main_model:
        env_models = [m.strip() for m in main_model.split(",") if m.strip()]
        # Prepend fast default models to env models
        combined = []
        for m in DEFAULT_FAST_MODELS + env_models:
            if m not in combined:
                combined.append(m)
        return combined
    return DEFAULT_FAST_MODELS
