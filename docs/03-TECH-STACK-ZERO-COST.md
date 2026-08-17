# 03 — Zero-Cost Tech Stack & Setup

**Project MAESTRO** · v1.0 · Target machine: **Apple M2 Pro, 16 GB, macOS 26.5**

> **Accuracy note.** Free-tier limits change without notice and my information has a cutoff. Every quota below is marked with a confidence level. **Verify the ones you depend on during Week 2 and record the actual observed limits in `docs/cost-log.md`.** That log is also a report artifact — a table of "advertised vs. observed free-tier limits, measured over 12 weeks" is a genuinely useful contribution to anyone replicating your work.

---

## 0. Fix This First ⚠️

Your default `python3` is **3.14.0b1** — a beta release. PyTorch, MLX, ChromaDB, spaCy, and `sentencepiece` either have no wheels for it or fail to build. You will lose days to this in Week 3 if you don't handle it now.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, in the project root:

```bash
uv python install 3.12 && uv venv --python 3.12 .venv && source .venv/bin/activate && python -V
```

`uv` installs an isolated 3.12 without touching your system Python, and it resolves dependencies far faster than pip — which matters when you rebuild environments on Colab and on the Windows machine. Confirm it prints `Python 3.12.x` before doing anything else.

Ollama is installed but not running (`could not connect to a running Ollama instance`). Start it with `ollama serve`, or just launch the Ollama app once and let it run as a background service.

---

## 1. The Stack At A Glance

| Layer | Choice | Cost | Why |
|---|---|---|---|
| Local LLM runtime | **Ollama** (+ MLX for fine-tuning) | Free, OSS | One-command models, OpenAI-compatible API, native Metal on M2 Pro |
| Planner model | **Qwen2.5-7B-Instruct Q4_K_M** | Free | Best-in-class structured/JSON output at 7B; ~4.7 GB |
| Interpreter model | **Qwen2.5-1.5B / Llama-3.2-3B** (LoRA fine-tuned) | Free | Small, fast, and the thing you actually train |
| Cloud fallback + baseline | Gemini / Groq / OpenRouter free tiers | Free tier | Frontier upper-bound for evaluation only |
| STT | **faster-whisper** (`base.en` / `small.en`) | Free, OSS | Runs locally, real-time on M2 Pro |
| TTS | **Piper** | Free, OSS | Local neural TTS, low latency, good voices |
| Wake word | **openWakeWord** | Free, OSS | Custom "Hey MAESTRO" model trainable for free |
| Orchestration | **LangGraph** | Free, OSS | Stateful DAG graphs; matches your synopsis |
| Validation | **Pydantic v2** | Free, OSS | The Action IR schema and its guarantees |
| Browser | **Playwright** | Free, OSS | Cross-platform, reliable selectors, headed/headless |
| Desktop control | PyAutoGUI · pyobjc/AppleScript · pywinauto | Free, OSS | Per-platform executors only |
| Classical NLP | **spaCy** + scikit-learn | Free, OSS | Intent baseline + NER |
| Fine-tuning | **MLX-LM** local · **Unsloth** on Colab/Kaggle | Free | See [05-NLP-AND-TRAINING](05-NLP-AND-TRAINING.md) |
| Vector store | **ChromaDB** | Free, OSS | Embedded, no server |
| Embeddings | `all-MiniLM-L6-v2` / `bge-small-en-v1.5` | Free, OSS | Local, 384-dim, fast |
| Relational store | **SQLite** | Free | Stdlib |
| Backend | **FastAPI** + Uvicorn | Free, OSS | Async, typed, auto-docs |
| Dev UI | **Textual** (TUI) | Free, OSS | Fast to build, demo-able, no frontend work in 7th sem |
| Final UI | **Electron** (per synopsis) or Tauri | Free, OSS | 8th semester only |
| Experiment tracking | **Weights & Biases** free tier / MLflow local | Free | Training curves for the report |
| Repo & CI | **GitHub** + Actions (public repo) | Free | Unlimited Actions minutes on public repos |
| Docs & diagrams | draw.io · Mermaid · dbdiagram.io | Free | §9 of Architecture |
| Reference manager | **Zotero** + Better BibTeX | Free | Start Week 2. Non-negotiable. |
| Writing | Overleaf free / LaTeX local | Free | IEEE template for the paper |

**Total cost: ₹0.** No credit card is required anywhere in this list.

---

## 2. Local Models — What Fits in 16 GB

Your M2 Pro's unified memory is shared between CPU and GPU, so budget conservatively: keep total model residency under ~10 GB so the OS, Chrome, and your Python process are not fighting for pages.

| Model | Quant | Size | tok/s (est.) | Role | Verdict |
|---|---|---|---|---|---|
| `qwen2.5:7b-instruct-q4_K_M` | Q4_K_M | 4.7 GB | ~25–35 | **Primary planner** | ✅ The default. Excellent JSON adherence. |
| `qwen2.5:3b-instruct` | Q4 | 2.0 GB | ~50–70 | Interpreter / fine-tune base | ✅ Fast, good enough post-fine-tune |
| `llama3.2:3b-instruct` | Q4 | 2.0 GB | ~50–70 | Alt fine-tune base | ✅ Compare against Qwen in ablation |
| `qwen2.5:14b-instruct` | Q4 | 9.0 GB | ~10–15 | Quality ceiling probe | ⚠️ Tight; nothing else can run |
| `qwen2.5-coder:7b` | Q4 | 4.7 GB | ~25–35 | Structured-output comparison | ✅ Worth one ablation row |
| `nomic-embed-text` | — | 0.3 GB | — | Embeddings | ✅ Or use MiniLM via sentence-transformers |

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M && ollama pull qwen2.5:3b-instruct && ollama pull nomic-embed-text
```

Measure actual throughput on your machine in Week 2 and put the numbers in `docs/cost-log.md`. Estimated tok/s figures are exactly the kind of thing an examiner will ask you to justify, and "we measured it" is the only good answer.

### Enforcing valid JSON
Ollama supports constrained decoding via a JSON schema in the `format` field. **Use it for the planner.** It converts "the model usually emits valid JSON" into "the model cannot emit invalid JSON," which removes an entire category of retry logic and makes your plan-validity numbers uninteresting in the best way.

---

## 3. Free Cloud APIs — For Baselines, Not Dependencies

These exist in your architecture for exactly two reasons: an upper-bound baseline in the evaluation, and a fallback if local inference is too slow on a teammate's weaker laptop. **Nothing in the critical path may require them.** A demo that dies because a quota reset at the wrong moment is a bad viva.

| Provider | Free tier (verify!) | Confidence | Best use |
|---|---|---|---|
| **Google AI Studio (Gemini)** | Generous free tier on Flash-class models; per-minute and per-day request caps; free tier data may be used for training | High that a free tier exists; **low on exact numbers** | Primary frontier baseline; also good for bulk dataset generation |
| **Groq** | Free tier, very high tokens/sec on open models (Llama, Qwen); daily caps | High / low on numbers | Fastest option; good when latency matters in a live demo |
| **OpenRouter** | Models with a `:free` suffix; strict rate limits; catalogue rotates | High / low | Convenient single API across many models for ablations |
| **Cerebras Cloud** | Free tier, extremely fast inference on open models | Medium | Alternative fast baseline |
| **GitHub Models** | Free for GitHub accounts, rate-limited; includes frontier models | Medium | Easy access to a strong baseline for comparison |
| **Mistral La Plateforme** | Free experimentation tier | Medium | Extra baseline row |
| **Hugging Face Inference** | Free credits/rate-limited serverless | Medium | Ad-hoc model probing |

**Privacy warning that belongs in your report.** Free tiers are frequently free *because* the provider may train on submitted data. A desktop agent's prompts contain file paths, folder structures, and document contents. Sending those to a free API is a real privacy leak, and it is precisely the argument for your local-first design. Frame it that way in the Justification chapter — it converts a constraint into a design rationale.

**Operational rules**
- API keys in `.env`, `.env` in `.gitignore`, `.env.example` committed. Never a key in a notebook you push.
- One provider-agnostic `LLMProvider` interface; swapping models must be a config change, not a code change — you will do this dozens of times during evaluation.
- Cache every cloud response by `hash(prompt+model+params)` in SQLite. Re-running an evaluation should not re-spend quota, and it makes results reproducible.

---

## 4. Free GPU For Fine-Tuning

| Option | Offer | Confidence | Notes |
|---|---|---|---|
| **Local MLX on M2 Pro** | Unlimited, free | High | LoRA on 1.5B–3B is comfortable in 16 GB. **Start here** — no queue, no disconnects. |
| **Kaggle Notebooks** | ~30 GPU-hours/week (T4 ×2 or P100) | High on the offer; verify current hours | Best free GPU allocation. Persistent datasets. Requires phone verification. |
| **Google Colab (free)** | T4, session/idle limits, no guaranteed availability | High | Fine for a 1–3 hour LoRA run. Save checkpoints to Drive every epoch — sessions do get reclaimed. |

Do the first fine-tune locally with MLX so you control the loop, then reproduce on Kaggle if you need a bigger base model or want to run several configurations in parallel.

---

## 5. macOS Permissions (do this in Week 2)

macOS will silently no-op automation calls if permissions aren't granted, which produces a maddening class of "it returns success but nothing happened" bugs. Grant them once, up front:

**System Settings → Privacy & Security →**
- **Accessibility** → add your terminal (and later the Electron app). Needed by PyAutoGUI and the AX API.
- **Automation** → allow your terminal to control Finder, System Events, Mail. Triggered on first AppleScript call.
- **Full Disk Access** → add your terminal *only if* you need to reach protected locations. Prefer not to — a narrower permission set is a better story for a safety project, and you should say so in the report.
- **Screen Recording** → only if you implement screenshot-based grounding (P2, probably skip).

Note in your report that MAESTRO deliberately operates with the *minimum* permission set that supports its verb registry. That is a genuine security property, and it is free — you get it by not asking for Full Disk Access.

---

## 6. Week-2 Setup Script

Run these in order from the project root.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
uv python install 3.12 && uv venv --python 3.12 .venv && source .venv/bin/activate
```

```bash
uv pip install "pydantic>=2" fastapi uvicorn langgraph langchain-core ollama httpx python-dotenv typer textual rich sqlite-utils chromadb sentence-transformers spacy scikit-learn pandas matplotlib playwright pyautogui pyobjc-framework-Quartz send2trash psutil faster-whisper pytest pytest-cov ruff mypy
```

```bash
python -m spacy download en_core_web_sm && playwright install chromium
```

```bash
brew install ollama piper && ollama serve
```

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M && ollama pull qwen2.5:3b-instruct && ollama pull nomic-embed-text
```

Then verify the whole chain end to end:

```bash
python -c "import ollama,json; r=ollama.chat(model='qwen2.5:7b-instruct-q4_K_M', messages=[{'role':'user','content':'Reply with JSON only: {\"ok\":true}'}], format='json'); print(json.loads(r['message']['content']))"
```

If that prints `{'ok': True}`, your local planner path works and you have a zero-cost LLM. That is Week 2's Track B deliverable in one command.

---

## 7. Repository Bootstrap

```bash
git init && git branch -M main
```

Create `.gitignore` before the first commit — models, venvs, and `.env` files must never enter history:

```gitignore
.venv/
__pycache__/
*.pyc
.env
data/generated/*.jsonl
eval/results/raw/
models/
*.gguf
*.safetensors
.DS_Store
chroma_db/
maestro.db
```

Branch protection isn't needed for a 3-person team, but do adopt one convention: **feature branches + PRs, even trivial ones.** Not for process theatre — because a year from now the PR history is the evidence of individual contribution, and viva panels ask who wrote what.

Add this CI check in Week 2 (`.github/workflows/ci.yml`) to enforce the cross-platform boundary from §7 of the Architecture doc:

```yaml
- name: No platform branching outside executors
  run: |
    ! grep -rn "sys.platform\|platform.system()" maestro/ \
      --include="*.py" | grep -v "maestro/executor/"
```

It costs nothing today and saves the architecture in month four.

---

## 8. Cost Accounting Table (for the report)

Keep this current in `docs/cost-log.md`; it is a direct answer to the "Economic Feasibility" line in your WPR, and it turns your budget constraint into a result.

| Item | Alternative commercial cost | Our cost | Mechanism |
|---|---|---|---|
| LLM inference (~50k planning calls over 28 weeks) | est. ₹15,000–40,000 in API fees | ₹0 | Local Ollama on owned hardware |
| Fine-tuning compute | est. ₹4,000–12,000 GPU rental | ₹0 | Local MLX + Kaggle free tier |
| Speech-to-text | est. ₹2,000 | ₹0 | faster-whisper, local |
| Text-to-speech | est. ₹3,000 | ₹0 | Piper, local |
| Vector database | est. ₹2,000/mo hosted | ₹0 | ChromaDB embedded |
| Hosting / CI | est. ₹1,500 | ₹0 | GitHub public repo |
| **Total** | **₹27,500–60,500** | **₹0** | |

Fill in the real commercial figures during Week 6 from current published price lists, and cite them. A defensible "we delivered a system that would otherwise cost ₹X" is a strong line in both the report and the viva.
