import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Groq chat model configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.1-8b-instant")

# gemini-embedding-001 outputs 3072 dimensions
EMBEDDING_DIM = 3072

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Qdrant
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "codebase_chunks")

# Chunking
MAX_CHUNK_LINES = 60
MIN_CHUNK_LINES = 5
CHUNK_OVERLAP_LINES = 5

# Hybrid search
DENSE_WEIGHT = 0.7
SPARSE_WEIGHT = 0.3
TOP_K = 8

# Supported languages
SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".md": "markdown",
}

IGNORE_PATTERNS = [
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "target", "vendor",
    "*.min.js", "*.bundle.js", "*.lock", "package-lock.json",
    "yarn.lock", "*.pyc", "*.class", "*.o",
]

# ── Hide API key from all logs ────────────────────────────────────────────
class _KeyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key in (GEMINI_API_KEY, GROQ_API_KEY):
            if not key:
                continue
            record.msg = str(record.msg).replace(key, "***REDACTED***")
            record.args = tuple(
                str(a).replace(key, "***REDACTED***") if isinstance(a, str) else a
                for a in (record.args or ())
            )
        return True

_filter = _KeyFilter()
for _handler in logging.root.handlers:
    _handler.addFilter(_filter)
logging.root.addFilter(_filter)
