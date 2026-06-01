"""Corpus-only Google ADK agent built on top of Vertex AI RAG retrieval."""

import os

import google.auth
import vertexai as _vertexai
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.genai import types

from .callbacks import (
    capture_retrieval_after_tool,
    validate_answer_after_model,
)
from .prompts import return_instructions_root
from .tools import retrieve_rag_documentation


load_dotenv()

if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
    try:
        _, _project_id = google.auth.default()
        if _project_id:
            os.environ["GOOGLE_CLOUD_PROJECT"] = _project_id
    except Exception:
        pass

# The RAG corpus has a region encoded in its resource path
# (projects/.../locations/<REGION>/ragCorpora/<ID>). The vertexai SDK's RAG
# calls use the globally-initialised location, NOT a location embedded in the
# resource. Derive the corpus's region from RAG_CORPUS so retrieval hits the
# right regional endpoint instead of the SDK default (us-central1).
_rag_corpus = os.environ.get("RAG_CORPUS", "")
if "/locations/" in _rag_corpus:
    _corpus_location = _rag_corpus.split("/locations/")[1].split("/")[0]
    os.environ["GOOGLE_CLOUD_LOCATION"] = _corpus_location
else:
    os.environ.setdefault(
        "GOOGLE_CLOUD_LOCATION",
        os.environ.get("AGENT_ENGINE_LOCATION", "europe-west1"),
    )
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

_vertexai.init(
    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
    location=os.environ["GOOGLE_CLOUD_LOCATION"],
)


root_agent = Agent(
    model=os.environ.get("AGENT_MODEL_NAME", "gemini-2.5-flash"),
    name="gra_corpus_agent",
    instruction=return_instructions_root(),
    tools=[retrieve_rag_documentation],
    after_tool_callback=capture_retrieval_after_tool,
    after_model_callback=validate_answer_after_model,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.1,
    ),
)


app = App(root_agent=root_agent, name="gra_corpus_agent")
