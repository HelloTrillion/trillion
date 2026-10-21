"""Latency bench (EVL-3): from the end of speech to the first sound of her reply.

    uv run python -m evals.latency.run --url "http://127.0.0.1:7777/#token=..."
    uv run python -m evals.latency.run --url ... --runs 20 --wav question.wav

Run by hand against a Trillion you started, with listening and a voice made in the
core (ElevenLabs, OpenAI, Piper) switched on. It makes no sound: the question is a
recording sent as the microphone would send it, in real time, and her reply is
measured as it arrives, never played.

The measurement is loopback, not milestones: the clock starts at the last loud
sample of the recording as it is sent, and stops at the first loud sample of her
reply, placed where the page would play it (when its first audio arrives, plus the
page's short lead). What it leaves out is the speaker's own output delay, a few
milliseconds on most machines. The first turn warms connections and is reported
apart. Every turn is also kept for `trillion doctor --trace`.

Each run is a real turn: it uses the model, the ears and the voice you chose, and
costs what they cost. With no --wav, macOS's `say` records the question.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from trillion.cli.trace import SPOKEN_P50, SPOKEN_P90, percentile

RATE = 16_000
FRAME_S = 0.02
FRAME_BYTES = int(RATE * FRAME_S) * 2
# A sample this loud or louder is sound: well above the hiss of a recording.
LOUD = 0.02
# The page starts playback this long after the first audio arrives (voice.ts LEAD_S).
PAGE_LEAD_S = 0.06
QUESTION = "In one short sentence, what is the tallest mountain in the world?"
WAIT_S = 60.0


def loud_samples(pcm: bytes, level: float = LOUD) -> list[int]:
    """Indexes of the samples at or above `level`, in PCM16."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    bar = level * 32768
    return [i for i, s in enumerate(samples) if abs(s) >= bar]


def last_sound_s(pcm: bytes) -> float:
    """Where the speech in a recording ends, in seconds from its start."""
    loud = loud_samples(pcm)
    return (loud[-1] + 1) / RATE if loud else 0.0


@dataclass(kw_only=True)
class Run:
    heard_s: float | None = None
    first_text_s: float | None = None
    audible_s: float | None = None
    error: str = ""


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise SystemExit(
                f"{path} must be 16 kHz mono 16-bit. Convert it with:\n"
                f"  sox {path} -r 16000 -c 1 -b 16 question.wav"
            )
        return w.readframes(w.getnframes())


def record_question(text: str) -> bytes:
    say = shutil.which("say")
    if say is None:
        raise SystemExit("No --wav given, and `say` is not here to record one. Pass --wav.")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "question.wav"
        subprocess.run(  # noqa: S603 - fixed program, our own arguments
            [say, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", text],
            check=True,
        )
        return read_wav(out)


class Bench:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.messages: asyncio.Queue[tuple[float, dict[str, Any]]] = asyncio.Queue()
        self.audio: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()

    async def read(self) -> None:
        async for raw in self.ws:
            at = time.perf_counter()
            if isinstance(raw, bytes):
                await self.audio.put((at, raw))
            else:
                await self.messages.put((at, json.loads(raw)))

    async def send(self, **body: Any) -> None:
        await self.ws.send(json.dumps(body))

    async def until(self, *kinds: str, timeout: float = WAIT_S) -> tuple[float, dict[str, Any]]:
        """The next message of one of these kinds. An error ends the run."""
        deadline = time.perf_counter() + timeout
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise TimeoutError(f"no {' or '.join(kinds)} within {timeout:.0f}s")
            at, msg = await asyncio.wait_for(self.messages.get(), left)
            if msg.get("type") in kinds:
                return at, msg
            if msg.get("type") == "error":
                raise RuntimeError(f"Trillion said: {msg.get('code')} {msg.get('kind') or ''}")

    async def turn(self, question: bytes) -> Run:
        run = Run()
        while not self.audio.empty():
            self.audio.get_nowait()
        await self.send(type="listen.start")
        await self.until("mic.start")
        # The question, as a microphone sends it: 20 ms at a time, in real time.
        started = time.perf_counter()
        ended = started + last_sound_s(question)
        sent = 0
        stopped = asyncio.ensure_future(self.until("mic.stop", timeout=30.0))
        silence = bytes(FRAME_BYTES)
        while not stopped.done():
            frame = question[sent : sent + FRAME_BYTES] if sent < len(question) else silence
            await self.ws.send(frame.ljust(FRAME_BYTES, b"\0"))
            sent += FRAME_BYTES
            await asyncio.sleep(max(0.0, started + sent / 2 / RATE - time.perf_counter()))
        _, stop = stopped.result()
        if stop.get("reason") != "end":
            run.error = f"listening ended: {stop.get('reason')}"
            return run
        at, _ = await self.until("transcript.final")
        run.heard_s = at - ended
        at, _ = await self.until("reply.delta")
        run.first_text_s = at - ended
        # Her reply: the first audio's arrival is when playback begins, and the quiet
        # before her first word is played before anything is heard.
        first_arrival: float | None = None
        quiet = 0
        turn_id = None
        while run.audible_s is None:
            at, frame = await asyncio.wait_for(self.audio.get(), WAIT_S)
            first_arrival = first_arrival or at
            turn_id = int.from_bytes(frame[1:5], "little")
            pcm = frame[9:]
            loud = loud_samples(pcm)
            if loud:
                audible = first_arrival + PAGE_LEAD_S + (quiet + loud[0]) / RATE
                run.audible_s = audible - ended
                # Tell Trillion too, as the page would, so `doctor --trace` has it.
                ahead = max(0.0, audible - time.perf_counter())
                await self.send(type="playback.started", turn_id=turn_id, in_ms=ahead * 1000)
            else:
                quiet += len(pcm) // 2
        await self.until("turn.complete")
        return run


async def bench(url: str, runs: int, question: bytes, gap_s: float = 8.0) -> int:
    from websockets.asyncio.client import connect

    parts = urlsplit(url)
    token = dict(p.split("=", 1) for p in parts.fragment.split("&") if "=" in p).get("token")
    if not token or parts.hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit(
            '--url must be the address Trillion printed: "http://127.0.0.1:PORT/#token=…"'
        )
    async with connect(f"ws://{parts.netloc}/ws", max_size=1 << 22) as ws:
        b = Bench(ws)
        reader = asyncio.ensure_future(b.read())
        await b.send(type="hello", token=token, protocol=1, client="bench")
        _, session = await b.until("session", timeout=10.0)
        speech, ears, model = session.get("speech"), session.get("listening"), session.get("model")
        if not model:
            raise SystemExit("No model is set up yet. Choose one in Settings first.")
        if not ears:
            raise SystemExit("Listening is off. Switch it on in Settings, then run this again.")
        if not speech or speech.get("where") != "core":
            raise SystemExit(
                "This needs a voice made in Trillion (ElevenLabs, OpenAI or Piper). The "
                "computer's own voice speaks in the page, where this cannot hear it."
            )
        say(
            f"{platform.system()} {platform.machine()}, Python {platform.python_version()}\n"
            f"model {model['provider']} {model['model']} · ears {ears['kind']} · voice "
            f"{speech['kind']}\n{runs} timed turns after one to warm up, {gap_s:g}s apart\n"
        )
        results: list[Run] = []
        for i in range(runs + 1):
            try:
                result = await b.turn(question)
            except (TimeoutError, RuntimeError) as e:
                result = Run(error=str(e))
            label = "warm-up" if i == 0 else f"run {i}"
            say(
                f"{label:>8}  words {_s(result.heard_s)}  text {_s(result.first_text_s)}"
                f"  heard {_s(result.audible_s)}  {result.error}"
            )
            if i:
                results.append(result)
            # People pause between turns, and an idle connection may be gone by then.
            await asyncio.sleep(gap_s)
        reader.cancel()
    heard = [r.audible_s for r in results if r.audible_s is not None]
    if not heard:
        say("\nNo turn was heard: see the errors above.")
        return 1
    p50, p90 = percentile(heard, 0.5), percentile(heard, 0.9)
    met = p50 <= SPOKEN_P50 and p90 <= SPOKEN_P90
    say(
        f"\nend of speech -> first sound of her reply, n={len(heard)} (warm)\n"
        f"  p50 {p50:.2f}s  p90 {p90:.2f}s   goal {SPOKEN_P50}s / {SPOKEN_P90}s: "
        f"{'met' if met else 'missed'}\n"
        f"  where the time went, turn by turn: trillion doctor --trace {runs + 1}"
    )
    return 0 if met else 1


def say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _s(value: float | None) -> str:
    return f"{value:5.2f}s" if value is not None else "    - "


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    args.add_argument("--url", required=True, help="the address Trillion printed when it started")
    args.add_argument("--runs", type=int, default=10)
    args.add_argument("--wav", type=Path, help="the question, 16 kHz mono 16-bit")
    args.add_argument("--say", default=QUESTION, help="what `say` records, with no --wav")
    args.add_argument(
        "--gap", type=float, default=8.0, help="seconds between turns, as a person pauses (8)"
    )
    opts = args.parse_args()
    question = read_wav(opts.wav) if opts.wav else record_question(opts.say)
    if not loud_samples(question):
        raise SystemExit("The recording is silent.")
    try:
        return asyncio.run(bench(opts.url, max(1, opts.runs), question, opts.gap))
    except KeyboardInterrupt:
        return 130
    except OSError as e:
        sys.stderr.write(f"Could not reach Trillion: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
