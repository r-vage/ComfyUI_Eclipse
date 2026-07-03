# ⚠️ Important Security Notice — Running LLMs Locally with ComfyUI

**Please read before using any Smart LM / LLM node that loads models from
Hugging Face or other public hubs via Python libraries.**

---

## TL;DR

If you load a model from a public hub using `transformers`, `torch.load`,
`pickle`, `joblib`, or any "convenience" LLM Python wrapper, **you are
running whatever code the model author put in that file** — on the same
user account that has access to your files, browser cookies, SSH keys,
crypto wallets, and ComfyUI workflows.

The safe answer for everyday use is:

> **Install Docker and run your LLM behind Ollama. Talk to it over HTTP
> from ComfyUI. Never load untrusted weights directly in the same Python
> process as ComfyUI.**

Eclipse's Smart LM Loader supports four Docker backends — **Ollama is
recommended for most users** because it is the only one that is safe on
*two* independent layers (see the three-tier comparison below).

---

## Why this matters — documented attacks (not theoretical)

These are real, public, dated incidents. None of this is speculation.

### 1. Malicious pickle models on Hugging Face — JFrog, Feb 2024
JFrog Security Research identified **~100 malicious models on Hugging Face**
containing real harmful payloads. The most notable case (`baller423/goober2`)
embedded a reverse shell via `pickle.__reduce__` that connected to a
hard-coded IP on `torch.load()`. The model card looked normal. Hugging Face
flagged it as "unsafe" but **did not block the download.**
Source: <https://jfrog.com/blog/data-scientists-targeted-by-malicious-hugging-face-ml-models-with-silent-backdoor/>

### 2. "nullifAI" — pickle scanner bypass, ReversingLabs, Feb 2025
Two HF models bypassed the Picklescan / HF malware scan by using a broken
pickle stream — the scanner errored out before reaching the malicious
opcode, but `torch.load()` still executed it. Real reverse shells were
delivered this way.
Source: <https://www.reversinglabs.com/blog/rl-identifies-malware-ml-model-hosted-on-hugging-face>

### 3. CVE-2023-6730 — `transformers` RCE via trusted-model loading
A *transitive* attack: loading a perfectly innocuous model could pull in a
second model (its tokenizer / dependency) that contained code execution.
The user only consciously downloaded one model.
Source: <https://nvd.nist.gov/vuln/detail/CVE-2023-6730>

### 4. PyPI / npm typosquatting targeting AI devs
Multiple campaigns through 2024–2025 published packages with names like
`openai-python`, `huggingface-cli`, `tensorflow-gpu-utils`, etc. that
exfiltrate `~/.aws/credentials`, `~/.ssh/`, browser cookies, Discord tokens,
and crypto wallets the moment they are `pip install`-ed.

### 5. `litellm` PyPI supply-chain compromise — March 2026
The **`litellm` PyPI package** — a widely-used LLM proxy/wrapper that
shows up as a transitive dependency in many AI tools — was compromised
for ~24 hours via a chained supply-chain attack:
- An attacker ("TeamPCP") first compromised **Trivy** (the security
  scanner), poisoned its releases, and harvested CI/CD secrets from any
  project that ran the malicious Trivy in CI.
- Those harvested secrets included the litellm maintainer's PyPI
  publish token. The attacker pushed **v1.82.7** and **v1.82.8** to
  PyPI directly, bypassing GitHub CI entirely (no matching git tags
  exist).
- **v1.82.8** added a `litellm_init.pth` file that runs on **every
  Python startup** of the venv — no `import litellm` required. The
  payload harvests SSH keys, environment variables, AWS/GCP/Azure/K8s
  credentials, crypto wallets, DB passwords, shell history, CI configs,
  encrypts them, and exfiltrates to `litellm.cloud` (lookalike of the
  official `litellm.ai`, registered hours before the upload).
- ComfyUI-Manager added these versions to its blacklist; ComfyUI now
  refuses to start if `litellm 1.82.7` / `1.82.8` are present.

Sources: <https://github.com/BerriAI/litellm/issues/24518> ·
<https://ramimac.me/trivy-teampcp/>

**Takeaway:** even legitimate packages can be hijacked. A venv contains
the blast radius to one Python environment (you can wipe and rebuild),
but on the host any code that runs as your user can still steal your
credentials — see *Baseline hygiene* below.

### 6. Hugging Face's own warning
HF marks pickle / `.bin` / `.pt` files as **unsafe** for a reason. They
publicly recommend `.safetensors` for weights, but **safetensors only protect
the weights — they do not protect arbitrary code in `modeling_*.py`,
`configuration_*.py`, `*.py` files in the repo, or `trust_remote_code=True`
flags.** Many popular LLMs require `trust_remote_code=True` to run — and
that flag means "run whatever `.py` files come with the repo, no questions."

---

## What Eclipse ships to reduce these risks (v3.5.17)

Eclipse can't make Hugging Face safe, and it can't make `transformers` safe,
but it does try to remove the easy footguns. As of **v3.5.17** the SML
subsystem has been hardened along several axes — none of these replace the
advice in this document, they just make the unsafe defaults less unsafe:

- **`trust_remote_code` is now default-deny.** Previously several SML
  loader paths hardcoded `trust_remote_code=True`. That is gone. The value
  is now controlled by a **per-model registry flag** (default `false`) and
  can only be enabled by:
  - flipping the flag on a specific model in
    `registry/transformers_models.json` (the curated entries for Florence-2
    and Mistral-3 / Pixtral already ship with the flag — those models
    genuinely need it), or
  - toggling the **"⚠ Trust Remote Code" chip** on the *Smart LM Loader*
    node at workflow time. The chip can only *enable* trust — it can never
    re-enable a model that the registry has marked safe. The *Smart
    Detection* node has no chip; its registry flags are the only switch.
- **Docker images are validated** against a conservative whitelist regex
  (`[a-z0-9._/-]+(:tag)?(@sha256:...)?`) before being passed to subprocess.
  Shell metacharacters, leading `-`, and absurd lengths are rejected.
- **Docker port bindings default to `127.0.0.1`.** The unauthenticated
  OpenAI-compatible APIs that vLLM / SGLang / Ollama / llama.cpp expose
  are no longer reachable from the LAN unless you explicitly set
  `docker_bind_host: "0.0.0.0"` in `docker_config.json`.
- **`llm_models_path` is validated.** The server endpoint that writes this
  config value rejects paths > 4096 chars, paths containing null bytes,
  and any `..` segment (after normalizing backslashes). Absolute paths
  are still allowed so USB / external drives keep working.

None of this turns `transformers`-style local loading into a safe operation —
the **only** layout that is actually safe is the Docker + Ollama
recommendation in the TL;DR. But if you do load a model in-process, the
registry flag and chip at least force you to opt in deliberately for each
individual model.

---

## What "running an LLM via the Python lib" actually means

When you do this in your ComfyUI process:

```python
AutoModelForCausalLM.from_pretrained("SomeAuthor/CoolNewLLM",
                                     trust_remote_code=True)
```

…you have just:

1. Downloaded `.py` files from a stranger's repo.
2. **Executed** those `.py` files inside the same Python interpreter that
   has full access to your `$HOME`, your saved API keys, your ComfyUI user
   folder, and (on most setups) your entire user account.
3. Loaded a pickle / unsafe tensor file that may itself execute code on load.

There is no sandbox. There is no permission prompt. There is no
"are you sure?" — exactly as if you had downloaded and double-clicked a
`.exe`.

---

## Baseline hygiene — always use a virtual environment, never system Python

Before the security ladder below, one ground rule applies to **every**
setup, Docker or not:

> **Run ComfyUI inside a dedicated Python virtual environment (venv,
> conda, uv, etc.). Never `pip install` anything into your system
> Python.**

### Is a venv a security sandbox?

**No.** Be honest with yourself about this. A Python venv is *not* a
security boundary. Code that runs inside a venv:

- runs as **your normal user account**,
- can read `$HOME`, `~/.ssh/`, browser cookies, crypto wallets, saved API
  tokens, and every ComfyUI workflow you own,
- can make outbound network connections,
- can write/modify any file your user can write to.

A malicious pickle loaded inside a venv is just as dangerous to your
user account as one loaded outside it. **A venv does not protect you
from a malicious LLM.** Only Docker / a separate user account / a VM
does that.

### So why does it still matter?

A venv is a **hygiene and blast-radius** layer, not a sandbox. It buys
you three concrete things:

1. **No `sudo pip install`.** Installing into the system Python on Linux
   usually requires `sudo`, which means a malicious `setup.py` or
   post-install script runs **as root** — free game over. A venv keeps
   every install user-level, no root needed, ever.
2. **You don't poison the OS.** On Linux/macOS, system Python is used by
   `apt`, `dnf`, system tooling, and other apps. Mixing ComfyUI's
   requirements (specific torch / transformers / numpy versions) into
   system Python eventually breaks something unrelated, and one bad
   package contaminates everything.
3. **Nuke-and-rebuild is one command.** `rm -rf venv/ && python -m venv
   venv && pip install -r requirements.txt` gets you a clean slate. If
   you ever suspect a typosquatted package or a compromised dependency,
   you can wipe the entire Python environment in seconds without
   touching the OS.

### Rule of thumb

- **Always:** ComfyUI lives in its own venv (Eclipse's install guides
  set this up for you). If you need a turnkey setup, see the official
  Eclipse install scripts: <https://github.com/r-vage/ComfyUI-Installation-Script-for-Linux>
  (Linux `install_comfy_env.sh` + Windows `install_comfy_env_win.ps1` —
  both create a dedicated venv automatically).
- **Never:** `sudo pip install <anything>` on your daily-driver machine.
- **Never:** install ComfyUI dependencies into the system Python
  interpreter that ships with your OS.
- **Treat venv as hygiene, Docker as security.** They solve different
  problems and you want both.

---

## The three-tier security ladder

All four Docker backends are dramatically safer than in-process
`transformers`, but they are not equal. The ladder from worst to best:

### 🔴 Worst — `transformers` directly in the ComfyUI Python process
- Loads pickle / `.bin` weights → **RCE on load.**
- `trust_remote_code=True` runs strangers' `.py` files **in-process.**
- Full access to your `$HOME`, SSH keys, browser cookies, wallets,
  ComfyUI workflows, Civitai / HF API tokens.
- One bad model = one compromised user account.

### 🟡 Better — vLLM / SGLang / llama.cpp-HTTP in Docker
- Still pulls models from the open Hugging Face registry.
- Still loads PyTorch `.bin` (pickle) weights when present.
- Still honors `--trust-remote-code` if you (or a default config) set it.
- **BUT** — the exploit now runs *inside the container*, not on your host.
  - No access to your home directory, keys, wallets, or workflows.
  - `docker rm -f <name>` wipes it clean in one command.
  - Network egress can be firewalled per-container.
- **Safe usage rule:** stick to `.safetensors` weights and never pass
  `--trust-remote-code` unless you've read every `.py` file in the repo.

### 🟢 Best for casual users — Ollama in Docker
Ollama is the strictest of the four because it is defense-in-depth on
**two independent layers**:

#### Layer 1 — Format: GGUF only
Ollama can only load **GGUF** files. GGUF is a plain tensor container
(similar in spirit to `.safetensors`):
- No `pickle` opcodes.
- No `__reduce__` trick.
- No embedded `.py` files.
- No `trust_remote_code` execution path.

Even a **deliberately malicious GGUF** cannot run code on load. The worst
it can do is produce bad outputs (gibberish, jailbreak attempts, bias).
That's a content problem, not a security problem.

#### Layer 2 — Registry: `ollama.com/library` is curated, not open upload
This is the part most people miss:

- **Hugging Face** = open upload. Anyone makes an account and uploads
  anything — including `baller423/goober2`. JFrog found ~100 malicious
  models there.
- **Ollama's official library** at <https://ollama.com/library> is
  maintained by the Ollama team. The popular tags (`llama3.2`,
  `qwen2.5`, `mistral`, `gemma3`, `deepseek-r1`, `phi4`, …) are **official
  builds packaged by Ollama itself** from the original lab weights.
- Users *can* push to their own namespace (`user/mymodel`), but those are
  clearly separated under a username prefix — same way Docker Hub
  separates `nginx` (official) from `someuser/nginx` (third-party).

When you do `ollama pull qwen2.5:14b`, you're pulling from the same kind
of trust source you'd use running the official `nginx` Docker image — not
from a random uploader.

(Pulling `ollama pull someuser/sketchy-model` is back to "trust a stranger"
territory, but it is **still only GGUF**, so still no code execution path.)

---

## Combined threat matrix

| Threat                                     | `transformers` (host) | vLLM / SGLang (Docker)  | Ollama (Docker)    |
| ------------------------------------------ | :-------------------: | :---------------------: | :----------------: |
| Code exec on model load                    | 🔴 yes (pickle)       | 🟡 yes, but contained   | 🟢 impossible      |
| `trust_remote_code` runs strangers' Python | 🔴 yes (in-process)   | 🟡 yes, but contained   | 🟢 not supported   |
| Anyone can publish a model                 | 🔴 yes (HF open)      | 🔴 yes (HF open)        | 🟢 curated library |
| Damage reaches your `$HOME` / keys         | 🔴 yes                | 🟢 no                   | 🟢 no              |
| Easy to wipe and start clean               | 🟠 manual             | 🟢 `docker rm`          | 🟢 `docker rm`     |
| Inference speed (consumer GPU)             | 🟡 OK                 | 🟢 fastest at scale     | 🟢 fastest casual  |
| Model catalog                              | 🟢 huge               | 🟢 huge                 | 🟢 huge            |
| One-time setup                             | 🟢 minutes            | 🟡 ~30 min              | 🟡 ~30 min         |

**Spend the 30 minutes once. Your future self will thank you.**

---

## The recommended setup: Docker + Ollama

Eclipse ships with full Docker install guides — please follow them instead
of guessing your way through it:

- **Linux:** [`Docker_Installation_Guide_Linux.md`](Docker_Installation_Guide_Linux.md)
- **Windows / macOS:** [`Docker_Installation_Guide.md`](Docker_Installation_Guide.md)

Both guides cover Docker engine setup, NVIDIA GPU passthrough, and starting
the Ollama / vLLM / SGLang / llama.cpp containers that Eclipse's Smart LM
Loader talks to.

### Then in ComfyUI

Use the **Smart LM Loader [Eclipse]** node, set the backend to **Ollama**,
point it at `http://localhost:11434/v1`, and select your pulled model. Done.

---

## "But I trust this one model from this one author"

That's fine — but please at least:

1. Prefer `.safetensors` weights. Refuse `.bin` / `.pt` / `.pkl` from
   unknown authors.
2. Avoid `trust_remote_code=True` unless the repo is a major lab
   (Meta, Qwen, Google, Mistral, Microsoft) **and** you read the `.py`
   files. Yes, actually read them.
3. Even then: prefer running via Ollama / vLLM / SGLang / llama.cpp in
   Docker. Almost every popular open model has a GGUF / Ollama version
   within days of release.
4. If you must run a model in-process, do it in a throwaway VM or a
   dedicated user account with no access to your real files / keys.

---

### Sources

- JFrog — *Data Scientists Targeted by Malicious Hugging Face ML Models with
  Silent Backdoor* (Feb 27, 2024)
  <https://jfrog.com/blog/data-scientists-targeted-by-malicious-hugging-face-ml-models-with-silent-backdoor/>
- ReversingLabs — *nullifAI: malicious ML models bypassing Picklescan*
  (Feb 2025)
  <https://www.reversinglabs.com/blog/rl-identifies-malware-ml-model-hosted-on-hugging-face>
- NIST — CVE-2023-6730 (transformers transitive RCE)
  <https://nvd.nist.gov/vuln/detail/CVE-2023-6730>
- BerriAI — *litellm PyPI v1.82.7 + v1.82.8 compromised* (Mar 2026)
  <https://github.com/BerriAI/litellm/issues/24518>
- Rami McCarthy — *Trivy / TeamPCP supply-chain analysis*
  <https://ramimac.me/trivy-teampcp/>
- Hugging Face — *Pickle scanning & security* docs
  <https://huggingface.co/docs/hub/en/security-pickle>
- Hugging Face — *safetensors*
  <https://github.com/huggingface/safetensors>
- Ollama — official model library
  <https://ollama.com/library>
- GGUF format spec
  <https://github.com/ggerganov/ggml/blob/master/docs/gguf.md>
- HiddenLayer — *Weaponizing ML models with ransomware*
  <https://hiddenlayer.com/research/weaponizing-machine-learning-models-with-ransomware/>
