# A local model with too little room

**Date:** 2026-11-24 · **Requirements:** BRN-2, VOI-14, SUR-8 · **Found by:** the nightly real-model CI job ([TESTING §3.4](../TESTING.md))

## Why

Ollama picks each model's context from the memory it can use. Without a large GPU that is 4,096 tokens. Her instructions alone are about 4,400 tokens, and with her tools her first request is about 13,300 (measured on `gemma4:e4b`). So it doesn't fit. Ollama either cuts it from the start, and she loses what she is and what she can do (a small model then never uses a tool), or it refuses the request. All she could say was "The model provider failed."

## Decisions

1. **Detect and tell; don't work around.** Ollama's OpenAI-compatible `/v1` endpoint ignores a context size in the request (tested with `options.num_ctx` and `num_ctx`). Setting it would mean a second provider path on Ollama's native API, to build and keep tested. The fix the user makes once is one setting, so Trillion names it.
2. **Read from Ollama itself.** `brain/ollama.py` asks the server she already talks to, at Ollama's own `GET /api/ps`, which lists the models loaded now with their `context_length`. It asks only when that server is on this computer or the user's own network, so a hosted service is never asked about Ollama's paths. Anything it can't tell is not known, never a warning: not Ollama, the model not loaded, an older Ollama that doesn't say, or no answer within 3 seconds.
3. **What is enough.** 32,768 tokens (`NEEDED`). Her first request takes about 13,300, so 16,384 would leave room for only a few turns.
4. **Where it is said.** These are the app's words, not hers (PER-11), and each one gives the fix: `OLLAMA_CONTEXT_LENGTH=32768`, or **Context length** in the Ollama app's settings.
   - **In her conversation, once a launch.** It comes after a reply, before the turn ends, as `notice{code: "context", text}`.
   - **In place of "The model provider failed"**, whenever a request is refused and the context is too small.
   - **In `trillion doctor`**, as a warning on the Model line. An Ollama that says it has enough shows its context on that line.
5. **Read once.** A model's context is kept for the launch once Ollama has said it, so a turn adds no request after the first.

## Known limits

- **Slow computers.** On a computer with no GPU, reading a 13,300-token request can take longer than the provider waits for a first byte (120 seconds). She then says the model "stopped answering", even with enough context. CI's 4-core runner shows it. Not fixed here.
- **Unloaded models.** The context is read only once the model is loaded, which is after her first request. If the model never loads, there is nothing to read.

## Tests

- `tests/brain/test_ollama_context.py`: reading `/api/ps` from the server's root, by name and as `:latest`. It covers each case that isn't known, and which servers are asked at all.
- `tests/api/test_ollama_context.py`:
  - told once a launch, with Ollama read once;
  - a refusal names the context;
  - enough room says nothing;
  - a hosted service is never asked.
- `tests/unit/test_doctor.py`: the warning and its fix, the context shown when it's enough, and a hosted model never asked. The shared fixture keeps the doctor tests off whatever Ollama the machine runs.
- `apps/web/test/view.test.ts`: the notice shows the app's line, and nothing without one.
- **Live**, against this computer's Ollama: `served_context` read 131,072 for a loaded `gemma4:e4b`, and nothing for a model that wasn't loaded or a server that wasn't there.
