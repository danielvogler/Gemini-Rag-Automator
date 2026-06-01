"""Deploy the corpus-only ADK agent to Vertex AI Agent Engine.

Idempotent: if an agent with the same ``display_name`` already exists, it is
updated in place; otherwise it is created. The resulting resource name is
written back to ``.env`` as ``AGENT_ENGINE_ID``.

Required env vars:
    GOOGLE_CLOUD_PROJECT, AGENT_ENGINE_LOCATION, AGENT_STAGING_BUCKET_NAME,
    AGENT_DISPLAY_NAME, AGENT_MODEL_NAME, GOOGLE_CLOUD_LOCATION (the corpus region).

Corpus identification (one of):
    RAG_CORPUS — full resource path projects/.../locations/.../ragCorpora/... .
                 Used as-is when set; Secret Manager is bypassed.
    GCP_SECRET_ID — name of a Secret Manager secret whose latest version
                    contains the corpus full resource path. Used as fallback.
"""

import logging
import os
import sys
from pathlib import Path

import vertexai
from dotenv import load_dotenv, set_key
from google.cloud import secretmanager
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error(f"Missing required env var: {name}")
        sys.exit(1)
    return value


def _fetch_corpus_resource_name(project_id: str, secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


def _resolve_corpus_resource(project_id: str) -> str:
    direct = os.environ.get("RAG_CORPUS")
    if direct:
        logger.info("[+] Using RAG_CORPUS from environment (Secret Manager bypass).")
        return direct.strip()

    secret_id = os.environ.get("GCP_SECRET_ID")
    if not secret_id:
        logger.error(
            "Neither RAG_CORPUS nor GCP_SECRET_ID is set. Provide RAG_CORPUS "
            "(full resource path projects/.../locations/.../ragCorpora/...) "
            "or GCP_SECRET_ID (Secret Manager secret holding that path)."
        )
        sys.exit(1)

    logger.info(f"[+] Fetching corpus resource name from Secret Manager: {secret_id}")
    try:
        return _fetch_corpus_resource_name(project_id, secret_id)
    except Exception as e:
        logger.error(
            f"Failed to read secret {secret_id} in project {project_id}: {e}\n"
            f"Hint: either run `make tf-apply && make init-rag` to bootstrap the "
            f"secret, or set RAG_CORPUS=projects/.../locations/.../ragCorpora/... "
            f"in your .env to deploy against an existing corpus directly."
        )
        sys.exit(1)


def main() -> None:
    load_dotenv(_ENV_FILE)

    project_id = _require_env("GOOGLE_CLOUD_PROJECT")
    agent_location = _require_env("AGENT_ENGINE_LOCATION")
    staging_bucket_name = _require_env("AGENT_STAGING_BUCKET_NAME")
    display_name = _require_env("AGENT_DISPLAY_NAME")
    model_name = _require_env("AGENT_MODEL_NAME")
    sa_id = _require_env("SERVICE_ACCOUNT_ID")
    service_account = f"{sa_id}@{project_id}.iam.gserviceaccount.com"

    corpus_resource = _resolve_corpus_resource(project_id)
    logger.info(f"    corpus: {corpus_resource}")

    staging_bucket_uri = f"gs://{staging_bucket_name}"
    logger.info(
        f"[+] vertexai.init project={project_id} location={agent_location} staging_bucket={staging_bucket_uri}"
    )
    vertexai.init(
        project=project_id,
        location=agent_location,
        staging_bucket=staging_bucket_uri,
    )

    sys.path.insert(0, str(_REPO_ROOT / "src"))
    from agent import root_agent  # noqa: E402

    # AdkApp.agent expects a BaseAgent, not an App wrapper. We still expose `app`
    # from src/agent/__init__.py for `adk web` / `adk run` local discovery; for
    # Agent Engine deployment we pass root_agent directly.
    wrapped = AdkApp(agent=root_agent, enable_tracing=True)

    # The agent module's import-time vertexai.init() pointed at the corpus's
    # region (so RAG retrieval hits the right endpoint at runtime). Re-init now
    # to AGENT_ENGINE_LOCATION so this script's agent_engines.create() targets
    # the Agent Engine region. The deployed container only runs the agent's
    # init, not this one.
    vertexai.init(
        project=project_id,
        location=agent_location,
        staging_bucket=staging_bucket_uri,
    )

    env_vars = {
        "RAG_CORPUS": corpus_resource,
        "AGENT_MODEL_NAME": model_name,
        "AGENT_ENGINE_LOCATION": agent_location,
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "AGENT_TOP_K": os.environ.get("AGENT_TOP_K", "10"),
        "AGENT_DISTANCE_THRESHOLD": os.environ.get("AGENT_DISTANCE_THRESHOLD", "0.6"),
        "AGENT_STRUCTURED_OUTPUT": os.environ.get("AGENT_STRUCTURED_OUTPUT", "0"),
    }

    requirements = [
        "google-adk>=1.31.0",
        "google-cloud-aiplatform[adk,agent-engines]>=1.135.0",
        "python-dotenv>=1.0.0",
    ]

    # extra_packages must be a relative path. Absolute paths are tarballed verbatim
    # (preserving the full filesystem path), which puts the agent module deep
    # inside the container instead of importable at root. We chdir into src/ so
    # the relative "./agent" tarballs as agent/__init__.py at the root.
    extra_packages = ["./agent"]
    src_root = _REPO_ROOT / "src"

    existing = [a for a in agent_engines.list(filter=f'display_name="{display_name}"')]

    prev_cwd = os.getcwd()
    os.chdir(src_root)
    try:
        if existing:
            target = existing[0]
            logger.info(f"[+] Updating existing agent: {target.resource_name}")
            remote_app = target.update(  # type: ignore[arg-type]
                agent_engine=wrapped,
                requirements=requirements,
                extra_packages=extra_packages,
                env_vars=env_vars,
                display_name=display_name,
                description="Corpus-only RAG agent. Strict abstain when retrieval is empty.",
                service_account=service_account,
            )
        else:
            logger.info(f"[+] Creating new agent: display_name={display_name}")
            remote_app = agent_engines.create(  # type: ignore[arg-type]
                wrapped,
                requirements=requirements,
                extra_packages=extra_packages,
                env_vars=env_vars,
                display_name=display_name,
                description="Corpus-only RAG agent. Strict abstain when retrieval is empty.",
                service_account=service_account,
            )
    finally:
        os.chdir(prev_cwd)

    logger.info(f"[+] Deployed: {remote_app.resource_name}")

    set_key(
        str(_ENV_FILE), "AGENT_ENGINE_ID", remote_app.resource_name, quote_mode="never"
    )
    logger.info(f"[+] Wrote AGENT_ENGINE_ID to {_ENV_FILE}")


if __name__ == "__main__":
    main()
