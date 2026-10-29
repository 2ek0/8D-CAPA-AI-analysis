# KAT AI Service

A Python (FastAPI) service that runs the AI analysis behind the KAT tools in nxportal, using Claude, OpenAI (GPT)
or Google Gemini.
The first tool is the **8D / CAPA AI Analysis** page
(`nxportal/Scripts/Pages/KatAi/KatAiTool/8DCAPAAiAnalysis.js`).

```
Browser ──file──▶ {ai}/api/kat/8dcapa/analyse  (KAT AI Service) ──PDF──▶ Claude / OpenAI / Gemini / local
        ◀─events─                                                 ◀─structured JSON─
```

## Setup

Requires Python 3.11+, and LibreOffice for `.pptx` files (PDFs work without it).

```powershell
cd C:\Users\goozhehao\source\repos\AI
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # then set AI_PROVIDER, that provider's API key, and SERVICE_KEY
```

Install LibreOffice from https://www.libreoffice.org and check `SOFFICE_PATH` in `.env`.

## Run

```powershell
uvicorn app.main:app --reload --port 8000
```

- Live test page: http://localhost:8000 (shows each slide's review as it arrives)
- Swagger UI: http://localhost:8000/docs
- Health check: http://localhost:8000/health
- Try it: `curl -F "file=@report.pptx" -H "X-Service-Key: <key>" http://localhost:8000/api/kat/8dcapa/analyse`

## Test

```powershell
pytest
```

The tests replace the AI with a fake, so they need no API key and cost nothing.

## Endpoints

### `POST /api/kat/8dcapa/analyse`

Multipart form with one field, `file` (`.pptx` or `.pdf`, up to `MAX_UPLOAD_MB`). This is the contract documented
in `requestCAPAAiAnalysis` in `8DCAPAAiAnalysis.js`.

With `Accept: text/event-stream` (what the page sends) the review is streamed as Server-Sent Events while the model
writes it, so each slide is shown as soon as it is reviewed:

```
event: start      data: {"slideCount": 12}
event: progress   data: {"slideNumber": 3, "message": "Checking evidence & data"}   (while a slide is being written)
event: slide      data: { one slide entry, see below }                            (one per slide, in order)
event: summary    data: { the whole result, see below }
event: error      data: { "Message": "..." }                                      (instead of summary, if it fails)
```

Without it, the whole result is returned as JSON at the end:

```jsonc
{
  "score": 0-100,
  "headline": "one line verdict on the whole report",
  "slides": [{
    "slideNumber": 1, "title": "...", "discipline": "D1".."D8" | null,
    "verdict": "good" | "warning" | "bad" | "unrelated",
    "logic": "sound" | "partial" | "unsound" | "na", "assessment": "...",
    "findings": [{ "type": "strength" | "issue" | "error" | "info", "text": "...", "evidence": "..." | null }],
    "suggestions": [{ "priority": "high" | "medium" | "low", "text": "..." }]
  }],
  "summary": {
    "overview": "...", "strengths": ["..."], "gaps": ["..."], "nextActions": ["..."],
    "disciplines": [{ "code": "D1", "status": "good" | "warning" | "bad" | "missing" }]
  }
}
```

Errors return `{ "Message": "..." }`, which the page shows as-is.

Read the stream with `fetch` and `response.body.getReader()` (`EventSource` only supports GET); see
`features/capa_8d/live.html`. A proxy in between must pass the stream through without buffering.

How it works: a `.pptx` is converted to PDF with LibreOffice (one page per slide) and the PDF is sent to the
model, which sees each slide's text and visuals. The answer is constrained to the Pydantic schema in
`schemas.py`. The score and D1-D8 coverage are calculated in `scoring.py`, with the same rules as the page's
mock, so the same review always gets the same score.

## Connecting nxportal

The page calls the service straight from the browser: `fetch(ResolveAIUrl("~/api/kat/8dcapa/analyse"))`, with
`credentials: "include"` and no service key. `ResolveAIUrl` (Site.Master) puts the path under `aiBase`, which is
Web.config appSettings `ai`, or `~/ai/` on nxportal itself when that is not set.

- **Through nxportal's `/ai/` path** (default): whatever forwards `/ai/...` to the AI backend must send
  `/ai/api/kat/8dcapa/...` here, without buffering the response.
- **Straight to this service**: set appSettings `ai` to this service's address (e.g. `http://localhost:8000/`) and
  `CORS_ORIGINS` here to the portal's address (e.g. `http://localhost:44300`), because the browser sends cookies.
- The page sends no `X-Service-Key`, so `SERVICE_KEY` must be empty when the page calls the service directly. The
  key is only for server-to-server callers; anything that can reach the service can use it, so keep it on an
  internal network or put it behind the portal's AI gateway before going live.
- `CAPAAiUseMockApi = false` in `8DCAPAAiAnalysis.js` uses this service; `true` plays the built-in sample.
  `CAPAAiMaxSizeMB` (50) matches `MAX_UPLOAD_MB`.

## Choosing the AI model

Set `AI_PROVIDER` in `.env` and fill in that provider's key and model, then restart. Nothing else changes: the
prompt, schema, scoring and the response the portal sees are the same for every provider.

| `AI_PROVIDER` | Key | Model setting | Example models |
|---|---|---|---|
| `claude` | `ANTHROPIC_API_KEY` | `CLAUDE_MODEL` | `claude-opus-5-5`, `claude-sonnet-5-5` (about half the price) |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-5`, `gpt-5-mini` |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `local` | none | `LOCAL_MODEL` | `gemma3:4b`, `qwen2.5:7b` (through Ollama on this machine) |

Any other model: use `AI_PROVIDER=openai` with `OPENAI_BASE_URL` pointing at an OpenAI-compatible service. For
example OpenRouter (`https://openrouter.ai/api/v1`) gives one key for models from many vendors, with
`OPENAI_MODEL` like `meta-llama/llama-4-maverick`. The model must accept PDF input and JSON schema output.

### Local models (Ollama)

1. Install Ollama from https://ollama.com and download a model: `ollama pull gemma3:4b`. Ollama runs in the
   background (tray icon); http://localhost:11434 should say "Ollama is running". `ollama run` is not needed.
2. In `.env`: `AI_PROVIDER=local`, `LOCAL_MODEL=gemma3:4b`, and a long `AI_TIMEOUT_SECONDS` (e.g. `3600`).

Local models can't read PDFs, so each slide's text is extracted and sent. With `LOCAL_SEND_IMAGES=true` each slide
is also sent as an image, for vision models (`gemma3`, `qwen2.5vl`). Data never leaves the machine, but small models
review less thoroughly and on a CPU-only laptop a review takes minutes per slide.

Model names change over time; check the provider's model list. Review quality differs between models, so try a few
known decks before switching production.

## Configuration

All settings come from environment variables or `.env`, see `.env.example`.

| Setting | Default | Notes |
|---|---|---|
| `SERVICE_KEY` | | Shared secret for server-to-server callers; empty disables the check (the portal page sends none) |
| `CORS_ORIGINS` | | Portal origins allowed to call from the browser, comma-separated; only when it calls this service directly |
| `AI_PROVIDER` | `claude` | `claude`, `openai`, `gemini` or `local` |
| `AI_TIMEOUT_SECONDS` | `600` | Per request to the model |
| `ANTHROPIC_API_KEY` | | Claude API key |
| `CLAUDE_MODEL` | `claude-opus-5-5` | |
| `CLAUDE_EFFORT` | `high` | `low` to `max`: more effort means a deeper review, more tokens and slower responses |
| `CLAUDE_MAX_TOKENS` | `64000` | Room for long decks |
| `CLAUDE_REFUSAL_FALLBACK` | `true` | Re-runs a declined request on a fallback model |
| `OPENAI_API_KEY` | | OpenAI key, or the key of the compatible service |
| `OPENAI_MODEL` | `gpt-5` | |
| `OPENAI_BASE_URL` | | Empty for OpenAI; set for OpenRouter, Azure or another compatible service |
| `OPENAI_REASONING_EFFORT` | | Reasoning models only; empty sends nothing |
| `GEMINI_API_KEY` | | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-2.5-pro` | |
| `LOCAL_BASE_URL` | `http://localhost:11434` | Ollama address |
| `LOCAL_MODEL` | `gemma3:4b` | Must be pulled first |
| `LOCAL_SEND_IMAGES` | `false` | Also send slide images (vision models only, slower) |
| `LOCAL_CONTEXT_TOKENS` | `32768` | Must fit the whole deck plus the answer |
| `SOFFICE_PATH` | `soffice` | LibreOffice executable |
| `MAX_UPLOAD_MB` | `50` | The page's limit. Claude takes ~32 MB per request and base64 adds ~33%, so it rejects PDFs over ~24 MB |

## Project layout

```
app/
  main.py               app factory, routers, /health
  config.py             settings
  errors.py             AppError and the { "Message" } error format
  security.py           X-Service-Key check
  clients/
    base.py             ModelClient interface, provider selection, shared error messages
    claude.py           Claude
    openai_client.py    OpenAI and OpenAI-compatible services
    gemini_client.py    Google Gemini
    local_client.py     local models through Ollama
  services/documents.py upload checks, .pptx to .pdf, page count
  features/
    capa_8d/            8D / CAPA AI Analysis
      router.py         POST /api/kat/8dcapa/analyse (JSON or streamed), GET /kat/8dcapa/live
      live.html         test page for the live review
      schemas.py        result contract
      prompt.py         review instructions for the model
      service.py        sends the deck to the chosen model, picks finished slides out of the stream
      scoring.py        score and discipline coverage
tests/
```

## Adding another KAT tool

Copy the layout of `features/capa_8d/` into a new `features/<tool>/` folder (router, schemas, prompt,
service) and register its router in `app/main.py`.

## Notes

- Report content is sent to the chosen provider's API (Anthropic, OpenAI, Google or the compatible service). Check
  each provider's data terms are acceptable for customer and supplier 8D reports.
- A 30 slide deck can take a minute or more to review; anything between the page and the service must allow for it.
- Each review logs its input and output token counts, which is what it is billed on.
