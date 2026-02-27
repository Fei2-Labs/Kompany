# Kompany

Autonomous business operating system for solo founders.

## Multi-Provider LLM Support

Kompany supports multiple LLM providers out of the box. Any model tier (apex/primary/economy) can use any provider:

| Provider | Models | SDK |
|----------|--------|-----|
| **Anthropic** | Claude Opus, Sonnet, Haiku | Native `anthropic` SDK |
| **OpenAI** | GPT-4o, GPT-4.1, o3, o4-mini | `openai` SDK |
| **Google Gemini** | Gemini 2.5 Pro, 2.5/2.0 Flash | `openai` SDK (compatible) |
| **GLM (Zhipu AI)** | GLM-4 Plus, Air, Flash | `openai` SDK (compatible) |
| **Kimi (Moonshot)** | Moonshot v1, Kimi | `openai` SDK (compatible) |
| **Custom** | Any OpenAI-compatible endpoint | `openai` SDK |

### Configuration

Set provider API keys via environment variables:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Default provider
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
export GLM_API_KEY="..."
export KIMI_API_KEY="..."
```

Override model tiers in `kompany.yaml`:

```yaml
models:
  apex: "gpt-4o"
  primary: "gemini-2.5-flash"
  economy: "glm-4-flash"
```

Or use a custom OpenAI-compatible endpoint:

```bash
export CUSTOM_LLM_BASE_URL="https://my-endpoint.example.com/v1"
export CUSTOM_LLM_API_KEY="..."
```

Provider detection is automatic based on model name prefix (e.g. `gpt-` → OpenAI, `gemini-` → Gemini). Unknown models route to the custom endpoint if configured, otherwise Anthropic.
