# tsunumon

An automated pipeline that turns a teaching request into a narrated educational
video — outline → script → consultant review → improvement → fact-check →
slide rendering → video composition. Built on FastAPI, Playwright (HTML slide
rendering), and pluggable LLM / TTS backends.

Every run executes the full pipeline from scratch.

---

## Disclaimer

This project can generate lessons aligned to AP® and IB subject topics, but it
contains **no** official AP or IB curriculum text — all teaching content is
generated at runtime by language models.

- AP® is a trademark registered by the College Board, which is not affiliated
  with, and does not endorse, this project.
- This work has been developed independently from and is not endorsed by the
  International Baccalaureate (IB).

---

## Architecture

```
Request ─▶ Router ─▶ Subject Classifier ─▶ Outline ─▶ Expand ─▶ Consultant
            └────────────────────────────────────────────────────┘
                                                                   │
                          Improve ◀──────────────────────────────┘
                            │
                            ▼
                       Fact-check ─▶ Render slides (Playwright) ─▶ Compose video (FFmpeg)
```

Each stage runs once per request.

- **LLM backends**: `mock` (hardcoded sample, no API key) or `claude` (Anthropic).
- **TTS backends**: `mock` (silent placeholder), `kokoro` (local model),
  `gcp`, `azure`.
- **Slides**: HTML rendered headlessly via Playwright/Chromium; third-party
  rendering libraries are vendored under `assets/libs/` (see
  `assets/libs/LICENSES.md`).

---

## Quick start (Docker)

The default configuration uses **mock LLM + mock TTS**, so it builds and runs
out of the box and produces a complete video (with placeholder audio) — ideal
for verifying the pipeline end to end without any API keys.

```bash
git clone <this-repo-url>
cd tsunumon
cp .env.example .env          # defaults are mock/mock — runnable as-is
docker compose up --build
```

Then call the API:

```bash
# Health check
curl http://localhost:8000/health

# Generate a video
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "demo-1",
    "course_requirement": "Teach the Banach Fixed-Point Theorem with an intuitive analogy, then a formal proof.",
    "student_persona": "A curious learner who prefers intuition before formal proofs."
  }'
```

The response contains a `video_url` (plus optional `subtitle_url` and
`supplementary_url`), served from `/files/`.

To run the pipeline directly without the HTTP server (in a local Python
environment — this entry point is not bundled into the Docker image):

```bash
pip install -e .
python run_local.py
```

---

## Real output (beyond mock)

To generate real lessons, edit `.env`:

| Setting | Purpose |
|---|---|
| `LLM_BACKEND=claude` + `ANTHROPIC_API_KEY=...` | Real lesson generation (required for non-mock output) |
| `TTS_BACKEND=kokoro` + mounted models | Real narration audio (see below) |
| `TTS_BACKEND=azure` + `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | Azure TTS |
| `TTS_BACKEND=gcp` + `GCP_API_KEY` | Google Cloud TTS |
| `STORAGE_BACKEND=gcs` + `GCS_BUCKET` / `GCS_PROJECT_ID` | Upload artifacts to GCS |

Optional enhancement stages (cover-image race, router, consultants, subject
classifier) are **disabled by default** and require their own API keys when
enabled — see `config/settings.py`. The pipeline runs fully without them.

### Competition configuration (finals)

The exact model configuration used for the official competition finals, for
reference and reproduction. Note that fact-checking ran on **Gemini** (Google
Search grounding), not the repository's default Claude backend.

| Stage | Model |
|---|---|
| Router | `gpt-5.4-mini` |
| Outline | `claude-opus-4-6` / `claude-sonnet-4-6` (router-selected) |
| Expand | `claude-haiku-4-5` |
| Consultants | `gpt-5.4` + `gemini-3.5-flash` (parallel) |
| Improve | `claude-sonnet-4-6` |
| Fact-check | `gemini-3.5-flash` (Google Search grounding) |
| Vision review — screen | `claude-sonnet-4-6` (1 pass) |
| Vision review — deep | `claude-opus-4-7` (all subjects, 2 passes — Fable 5 was unavailable during the finals, so deep review ran entirely on Opus 4.7) |
| Cover image judge | `gemini-3.5-flash` |
| Cover prompt generation | `gpt-5.4-mini` |
| TTS | Azure `en-US-RyanMultilingualNeural` (GCP Neural2 fallback) |

Finals production switches (`.env`):

```bash
ROUTER_ENABLED=true
CONSULTANT_ENABLED=true
FACT_CHECK_BACKEND=gemini
COVER_IMAGE_ENABLED=true
SUBJECT_CLASSIFIER_ENABLED=true
OPUS_REVIEW_MODEL=claude-opus-4-7   # Fable 5 was unavailable during the finals
```

Reproducing finals-grade output requires API keys for Anthropic, OpenAI,
Google (Gemini), and Azure Speech.

### Real narration TTS (kokoro)

The Kokoro TTS model files are not bundled (too large for the repo). To use real
narration, download the Kokoro ONNX model + voices and mount them at runtime:

```bash
docker run -v /path/to/models:/app/models -e TTS_BACKEND=kokoro ...
```

---

## Fact-check knowledge base

During the Improve / Fact-check stages the pipeline consults a curriculum
knowledge base via `web_fetch`. The default KB is hosted on GitHub Pages at
[`https://tsun-u.github.io/tsunumon-kb`](https://tsun-u.github.io/tsunumon-kb)
and contains independently-written study-reference notes for biology,
computer science, mathematics, and physics. `SubjectClassifier` fetches each
subject's `index.md` at startup to build the unit catalog, then injects the
most relevant unit URLs into the LLM prompt so `web_fetch` can pull them.

To allow additional KB hosts (e.g. your own deployment), add them to
`.env`:

```bash
WEB_FETCH_EXTRA_DOMAINS=my-kb.example.com
```

If you want to keep KB references locally instead, place markdown under
`config/curriculum/<subject>/`; when present, the local files take precedence
over the online catalog. The JS-library rendering KB under
`config/curriculum/tools/` is included.

---

## Tests

```bash
pip install -e .
pytest tests/
```

---

## License

MIT — see [LICENSE](LICENSE). Vendored third-party libraries under
`assets/libs/` retain their own licenses; see
[`assets/libs/LICENSES.md`](assets/libs/LICENSES.md).
