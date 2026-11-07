# Recall by meaning: she finds what you meant, not only what you said

**Date:** 2026-11-07 · **Requirements:** MEM-2 (meaning path), ONB-7, ONB-8 · **Builds on:** [memory](2026-10-10-memory.md), the verified model download built for local Whisper (VOI-11b)

Today a memory is found by the words it shares with the question. "How do I take my coffee?" finds "I take my coffee black." "What do I drink in the morning?" finds nothing, and neither does "where does my sibling live?" for "My sister Ana lives in Lisbon." This change adds the second half of MEM-2: search by meaning, with a small embedding model that runs on the machine. The keyword path stays, and still decides when a name or number is asked for.

## Decisions

1. **Optional, and off until it is installed.** Meaning search needs a runtime (`onnxruntime`, `tokenizers`, `numpy`) and a model file. The runtime is an optional install, `trillion[meaning]`, as `trillion[whisper]` is: it is large, and memory works without it. Without either part, recall is exactly what it was: by words.
2. **The model is `bge-small-en-v1.5`, 384 dimensions, fetched once by `trillion setup` (ONB-7).** Two files from BAAI's own repository on Hugging Face, pinned to one revision: `onnx/model.onnx` (133 MB) and `tokenizer.json`. Each is checked against the SHA-256 and size written in this repository. A file that does not match is deleted and never used. They live in `~/.trillion/models/bge-small-en-v1.5/`.
3. **Nothing is fetched at runtime (ONB-8).** The running app never downloads. `trillion setup` fetches the model only when the `meaning` runtime is installed, and `trillion setup --offline` skips it. A failed fetch is a line in setup's output, not a failure: memory still works by words, and running `trillion setup` again finishes it. The airplane test is unchanged and still passes.
4. **The files are checked again when they are loaded.** The first time a running app embeds anything, it hashes both files. A file changed since it was fetched turns meaning search off for that run, with a warning in the log. It is never loaded.
5. **Vectors live in SQLite, as plain BLOBs, searched by brute force.** Migration 021 adds `memory_vectors`: one row per memory, holding the model's name, a SHA-256 of the text it was made from, and 384 float32 values. The PRD names `sqlite-vec`. It is not used, for three reasons. It is a loadable extension, and not every Python build can load extensions. It is a native binary per platform, one more thing to ship and trust. And it is not needed: memory is capped at 5,000 active entries (MEM-6b), and scoring 5,000 vectors of 384 numbers is a few tens of milliseconds, exact, with no index to keep in step. The BLOB is the same little-endian float32 layout `sqlite-vec` reads, so moving to it later is a copy, not a re-embed.
6. **Keyword and meaning are blended so an exact name or number still wins.** Each memory's keyword score is divided by the best keyword score for that question, so the best keyword match scores 1. Its meaning score is its cosine similarity above a floor, scaled to 0–1, and counted at half weight. So a memory found by meaning alone scores at most 0.5 and can never outrank the best keyword match. Among weaker keyword matches, meaning reorders. A memory is a result only if it shares a word or clears the floor.
7. **The floor is 0.6 for this model.** Measured on a small set: related pairs score 0.6–0.83 ("who is my doctor for teeth" → the dentist, 0.69), and unrelated questions ("tell me a joke", "the weather tomorrow") top out at 0.54. The floor keeps every message from bringing along whatever memory happens to be nearest. The cost: a very loose match ("what do I drink in the morning?" → coffee, 0.51) is still missed. The floor belongs to the model, so a different model brings its own.
8. **A vector is remade when its memory's text changes.** A vector counts only if its text hash matches the memory's text now. A new memory is embedded as it is written. A file edited by hand is adopted at startup (as today), its old vector no longer matches, and it is embedded again. Memories without a current vector are embedded in the background after startup, a batch at a time, so a long memory never holds up the first message. Until then they are found by words.
9. **Retired and forgotten memories are never returned as current.** Meaning search scores the same pool as the keyword path: current memories only, unless history is asked for (MEM-7). A missing file's memory is not in the pool (MEM-6b). Vectors are derived data and are kept for retired memories, so history can be searched by meaning too.
10. **Anything that goes wrong falls back to words.** A missing runtime, a missing or changed file, or an error while embedding: the search answers by keyword, logs the kind of failure (never the text), and stops trying meaning for the rest of that run.
11. **Nothing else about recall changes.** Results are still "what was said" (MEM-5), still wrapped as untrusted, still cut to the recall bound (MEM-6b), still stamped with when they were said (MEM-8). Meaning only changes which memories are found and in what order.

## What the user sees

- Asked "where does my sibling live?", she finds "My sister Ana lives in Lisbon." Before, she found nothing.
- `trillion setup` prints `setup  fetched the meaning model (127 MB)` the first time, when `trillion[meaning]` is installed.
- `trillion doctor` has a **Meaning** line:
  - `ok    on: bge-small-en-v1.5`
  - `info  off: memory is found by its words` → `Install trillion[meaning], then run: trillion setup`
  - `info  off: the model is not downloaded` → `Run: trillion setup`

## How

- `memory/meaning.py`: the `Embedder` interface (`model`, `floor`, `embed(texts, query=)` returning unit vectors), the pinned model pack, `local(folder)` that returns the ONNX embedder only when the runtime and both files are present, `state(folder)` for doctor, and `fetch(folder)` for setup. It reuses `voice/models.py`'s download, which now makes a file's subfolder (`onnx/`).
- `OnnxEmbedder` loads lazily, off the event loop: checks both files' hashes, then runs the model on CPU, takes the first token's output, and normalises it. Questions get bge's search prefix; memories do not.
- `memory/rank.py` gains `scores()`, the keyword score per memory; `rank()` is unchanged in behaviour and built on it. `blend()` combines the two.
- `MemoryBook` takes an optional embedder. `recall()` stays the keyword path. New `search()` is the hybrid path, used by `memory_recall`, by the memories that come along with a message, and by `related()` (the "still held on the same subject" hint after a write). Embedding and scoring run in a worker thread.
- `SqliteMemoryStore` reads and writes `memory_vectors`. Only storage code touches the database, as before.
- The runtime builds the embedder with `meaning.local(models_dir)`, starts the background catch-up after the startup sync, and stops it before the database closes.

## Tests

- Ranking with a fake embedder (a handful of fixed concepts): a question with no shared word finds the right memory; an exact name beats a closer meaning; nothing under the floor is returned; a retired or forgotten memory is never current.
- Fallback: no embedder, an embedder that raises, and a model folder without files all answer by keyword.
- Vectors: written with a memory, remade when its file is edited by hand, made in the background for memories that have none, and stored as 384 float32 values.
- Checksum: a download whose bytes differ is refused and leaves no file; a file changed after download is refused at load.
- Migration 021 applies and the table holds what it should.
- Doctor reports each state of the Meaning line. The airplane test still passes.

## Not now

- An offline install pointing at a local folder of model files (ONB-7's last sentence). The download already verifies; copying from a folder with the same check is small, and belongs with the installer work.
- A Settings switch and a download button in the app, as Whisper has. The CLI is the one way in for now.
- MEM-4's duplicate removal by similarity. It can use these vectors once the summarizer exists.
- Quality evals for recall by meaning, to tune the floor and weight on more than a handful of examples.
- Languages other than English. `bge-small-en-v1.5` is English; another model would bring its own floor.
