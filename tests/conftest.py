import os
import sys
import tempfile
from pathlib import Path

# offline + isolated settings for tests (must be set before config.settings is imported)
os.environ["EMBEDDING_BACKEND"] = "hashing"
os.environ["PINECONE_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["LLM_FALLBACK_PROVIDER"] = "none"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["SQLITE_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
