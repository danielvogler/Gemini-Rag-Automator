# AGENTS.md

You are setting up, running or changing **Gemini RAG Automator**: a Terraform-provisioned
pipeline that turns PDFs dropped in a GCS bucket into a Vertex AI RAG corpus, plus a Google
ADK agent that answers questions **only** from that corpus and abstains when it cannot.

Read this file top to bottom before running anything. The order things come up in matters,
and two of the steps cannot be undone.

---

## Ask the operator first

Do not start provisioning until you know:

1. **Which GCP project?** Never rely on the ambient `gcloud config` default — every command
   below passes `--project` explicitly, and so should you.
2. **Which region?** See [Regions](#regions). This is not a free choice and it is painful to
   change later.
3. **What is going into the bucket?** Whatever goes in becomes retrievable by anyone who can
   reach the agent. If the documents are confidential, that needs a decision before upload,
   not after. If nobody can tell you the classification, treat it as not public and ask.
4. **Does the project already have a Firestore database?** A project gets exactly one
   `(default)` database, and its mode cannot be changed afterwards. Check before applying:
   ```bash
   gcloud firestore databases list --project <PROJECT_ID>
   ```
   If one exists in Datastore mode, stop and talk to the operator — `terraform apply` will
   fail, and the fix is not something to improvise.

---

## Prerequisites

| | |
|---|---|
| Python | 3.12+ (`.python-version` pins 3.12) |
| Dependencies | [`uv`](https://docs.astral.sh/uv/) — `uv sync --all-extras --dev` |
| Infra | Terraform |
| Auth | `gcloud auth application-default login` |

**Never create or download a service-account key file.** Application Default Credentials for
people, the attached service account for workloads. A `*.json` key in this repo is a leak.

---

## Setup, in order

Each step depends on the one before it.

```bash
cp .env.example .env         # 1. then fill it in — see Environment below
make init                    # 2. terraform init
make tf-plan                 # 3. read the plan before applying
make tf-apply                # 4. buckets, SA, IAM, Firestore, Cloud Function
make init-rag                # 5. creates the RAG corpus, writes its id to Secret Manager
```

### 6. Grant the Agent Engine service identity `actAs` on the service account

Terraform cannot do this one, because the Agent Engine service identity does not exist until
Vertex AI has been used in the project. One-time, per project:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  ${SERVICE_ACCOUNT_ID}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" \
  --project=${GOOGLE_CLOUD_PROJECT}
```

### 7. Deploy the agent and load documents

```bash
make agent-deploy                                  # writes AGENT_ENGINE_ID back into .env
gcloud storage cp paper.pdf gs://$GCS_BUCKET_NAME/ --project $GOOGLE_CLOUD_PROJECT
make list-files                                    # confirm it was ingested
make agent-query Q="what is geothermal energy"
```

---

## Verify it actually works

Ingestion is asynchronous and most failures here are silent by design, so check all three:

| Probe | Expected |
|---|---|
| `make agent-query Q="hi"` | A normal conversational reply. No retrieval, no citations. |
| `make agent-query Q="<something in your corpus>"` | An answer with inline `[n]` citations and a `Source excerpts:` section quoting the passages. |
| `make agent-query Q="who won the 2019 cricket world cup"` | Exactly `I cannot answer that from the available corpus.` |

If the second probe shows filenames like `a7f3.pdf` instead of `Title — Authors (Journal)`,
metadata extraction failed. It is best-effort and swallows its own errors, so look in the
Cloud Function logs rather than expecting a failure to surface.

---

## Regions

The RAG corpus, Agent Engine and the Gemini model must all sit in **one** region.

**`europe-west6` (Zürich) hosts RAG corpora but does not serve Gemini models**, so the agent
cannot run there. If data residency forces `europe-west6`, that constraint wins and this
design has to change — raise it rather than quietly picking another region.

Otherwise use `europe-west1`, `europe-west4` or `us-central1`, and set
`GOOGLE_CLOUD_LOCATION` and `AGENT_ENGINE_LOCATION` to the same value.

`FIRESTORE_LOCATION` is separate and uses [Firestore's own location
names](https://cloud.google.com/firestore/docs/locations) — for Europe that is usually `eur3`.

---

## Environment

| Variable | Notes |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | Target project. |
| `GOOGLE_CLOUD_LOCATION` | Region for the corpus and ingestion. |
| `GCS_BUCKET_NAME` | Source bucket. Must be globally unique. |
| `RAG_CORPUS_DISPLAY_NAME` / `RAG_CORPUS_DESCRIPTION` | Corpus metadata. |
| `GCP_SECRET_ID` | Secret Manager entry holding the corpus id. |
| `GEMINI_MODEL_NAME` | Model used for ingestion-time metadata extraction. |
| `SERVICE_ACCOUNT_ID` | Runs **both** the ingestor function and the deployed agent. |
| `CLOUD_FUNCTION_NAME` | Ingestor function name. |
| `FIRESTORE_LOCATION` | **Required.** `terraform apply` fails if unset. |
| `AGENT_ENGINE_LOCATION` | Must equal `GOOGLE_CLOUD_LOCATION`. |
| `AGENT_MODEL_NAME` | `gemini-2.5-flash`. |
| `AGENT_DISPLAY_NAME` | How deploys find and update an existing agent. Keep it stable. |
| `AGENT_STAGING_BUCKET_NAME` | Provisioned by Terraform. |
| `AGENT_ENGINE_ID` | Written automatically by `make agent-deploy`. |
| `AGENT_STRUCTURED_OUTPUT` | `1` for a JSON envelope, `0` for text + excerpts. |
| `AGENT_EXCERPT_MAX_CHARS` | Per-excerpt cap, `0` disables truncation. |
| `RAG_CORPUS` | Optional full corpus path; bypasses Secret Manager. |

Secrets belong in Secret Manager, fetched at runtime. Never commit `.env`.

---

## Commands

**Infrastructure** — `make init`, `make tf-plan`, `make tf-apply`, `make tf-destroy`
**Corpus** — `make init-rag`, `make list-files`, `make ingest-test`
**Agent** — `make agent-deploy`, `make agent-query Q="..."` (add `STRUCTURED=1`), `make agent-run`, `make agent-delete`
**Hygiene** — `make test`, `make pre-commit`, `make clean`

`make tf-destroy` and `make agent-delete` are destructive. Say what will be destroyed and get
an explicit yes before running either. Note that the Firestore database is deliberately left
behind on destroy (`deletion_policy = "ABANDON"`).

---

## Code map

| Path | What lives there |
|---|---|
| `terraform/` | Buckets, service account, IAM, Secret Manager, Firestore, Cloud Function. |
| `src/ingestor/` | The Cloud Function. Imports into the corpus, and extracts paper metadata. |
| `src/local_ingestor/` | `gra-ingest` CLI — runs the production handler by hand, bypassing Eventarc. |
| `src/agent/` | The ADK agent. |
| `src/agent/tools.py` | `retrieve_rag_documentation` — the agent's only tool. Joins in paper metadata. |
| `src/agent/prompts.py` | System instruction and `ABSTAIN_MESSAGE`. |
| `src/agent/callbacks.py` | Forces retrieval, validates the answer, appends excerpts. |
| `src/agent/rendering.py` | Excerpt/JSON rendering. Deliberately free of `google.adk` imports so it is unit-testable. |
| `scripts/` | Corpus init, file listing, agent deploy/query, legacy query. |
| `tests/` | pytest. |

### How the guarantee is enforced

The agent is given exactly one tool and no `google_search`, so there is no server-side
grounding path that can blend the corpus with the model's training memory. Then
`callbacks.py` does the rest:

- `before_tool_callback` records that retrieval happened and stashes the chunks in
  `temp:`-prefixed state, which ADK resets per invocation, so turn N cannot be validated
  against turn N−1's chunks.
- `after_model_callback` runs **only** when retrieval happened this turn. It checks every
  `[n]` the answer cites against the chunks actually retrieved. Unsupported ⇒ the whole
  answer is replaced with `ABSTAIN_MESSAGE`. Supported ⇒ excerpts are appended to the
  agent's own message, which is why they appear in the Cloud Console playground and not only
  in the CLI.
- Chitchat never triggers retrieval, so it passes through untouched.

If you change any of this, the three probes above are the regression test.

---

## Conventions

- **Style** — `ruff check .` and `ruff format --check .`. Both run in CI and pre-commit.
- **Tests** — pytest, and CI runs coverage. Write the test first; a behaviour without a test
  will be broken by the next person. Name tests for the behaviour, not the function.
- **Immutability** — build new dicts rather than mutating what you were handed.
- **Errors** — handle them explicitly. The metadata path is the one deliberate exception: it
  swallows failures so citation enrichment can never block ingestion. If you add another
  swallow, say in a comment why, because silent degradation is very hard to debug here.
- **Commits** — `<type>(<scope>): <subject>`, imperative, under 72 characters. Body in
  bullets explaining *why*. No absolute paths, no `Co-Authored-By` trailer, nothing that only
  makes sense if you were in the room.
- **Files** — many small ones. 800 lines is the ceiling.

---

## Known failure modes

**`stream_query` returns an empty 200 for every question.** The container installed a newer
`google-adk` than the one that pickled the agent, so it unpickled into a broken object.
`scripts/deploy_agent.py` pins both `google-adk` and `google-cloud-aiplatform` to the
versions installed locally at deploy time. If you loosen those pins, this comes back, and it
comes back silently.

**Citations show filenames instead of paper titles.** Metadata extraction failed somewhere
and was swallowed. The usual causes are the ingestor lacking read access to the source bucket
(`roles/storage.objectViewer`, granted in `terraform/main.tf`), Firestore not reachable, or
Gemini returning something unparseable for a PDF whose first page is a scan.

**`terraform apply` fails on the Firestore database.** The project already has a `(default)`
database. See [Ask the operator first](#ask-the-operator-first).

**Answers are suspiciously well-informed.** If the agent answers something that is definitely
not in the corpus, the defence has been bypassed — check that no additional tool was added
and that `after_model_callback` is still wired up.
