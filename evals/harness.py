"""The shared runner for the quality evals (EVL-1, EVL-2, EVL-6). Run by hand, never in CI.

Every eval takes the same options (`--model`, `--url`, `--judge-model`, `--judge-url`,
`--only <case>`, `--json <path>`) and exits the same way: 0 pass, 1 fail, 2 could not
run (no model answered, something missing). Each prints the judge first, then one line
per case, its totals and its verdict, and writes the same as JSON to evals/results/
(gitignored), which is what a later stage keeps as a baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO

import aiosqlite.core
import httpx
from tests.api import test_server
from tests.api.test_server import App, Chat, reply
from tests.brain.fakes import is_naming
from tests.voice.audio import frames, room, tone
from tests.voice.fakes import FakeSTT

from trillion.api.runtime import ListenSettings
from trillion.attention import quiet
from trillion.brain import config
from trillion.brain.config import ANTHROPIC_KEY, LOCAL_URL, ConfigError, ModelSettings
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.brain.types import (
    ModelEvent,
    ModelProvider,
    Msg,
    Pricing,
    ProviderError,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
)
from trillion.secrets.store import MemoryStore

PASS, FAIL, CANNOT_RUN = 0, 1, 2
LOCAL_MODEL = "gemma4:e4b"
RESULTS = Path(__file__).resolve().parent / "results"


class CannotRun(Exception):
    """The eval could not run at all: no model answered, or something is missing."""


PATCHED = "_trillion_eval_daemon"


def daemon_connection_threads() -> None:
    """aiosqlite's worker thread is not a daemon, so a run that stops before a connection
    is closed would keep the process from exiting. The same patch as tests/conftest.py,
    for runs by hand; once, however often it is asked for."""
    original = aiosqlite.core.Connection.__init__
    if getattr(original, PATCHED, False):
        return

    @functools.wraps(original)
    def init(self: aiosqlite.core.Connection, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        self._thread.daemon = True

    setattr(init, PATCHED, True)
    aiosqlite.core.Connection.__init__ = init  # type: ignore[method-assign]


daemon_connection_threads()


def options(about: str, argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The options every eval takes."""
    p = argparse.ArgumentParser(
        description=about, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", default=LOCAL_MODEL, help="the model under test")
    p.add_argument("--url", default=LOCAL_URL, help="its OpenAI-compatible address")
    p.add_argument(
        "--judge-model", default=LOCAL_MODEL, help="the local judge, when no Anthropic key is set"
    )
    p.add_argument("--judge-url", default=LOCAL_URL, help="the local judge's address")
    p.add_argument(
        "--only", action="append", default=[], metavar="CASE", help="run only this (repeatable)"
    )
    p.add_argument("--json", type=Path, default=None, metavar="PATH", help="write results here")
    return p.parse_args(argv)


def run(
    main: Callable[[argparse.Namespace], int],
    argv: Sequence[str] | None = None,
    *,
    about: str = "",
) -> int:
    """Parse the options, run the eval, and turn "could not run" into exit code 2:
    anything that stops it before a verdict (a stalled model, a report that could not
    be written, a broken session) is never counted as a fail. Ctrl-C still stops it."""
    opts = options(about, argv)
    try:
        return main(opts)
    except CannotRun as e:
        sys.stderr.write(f"cannot run: {e}\n")
        return CANNOT_RUN
    except Exception as e:
        sys.stderr.write(f"cannot run: {type(e).__name__}: {e}\n")
        return CANNOT_RUN


def pick[C](
    cases: Sequence[C], only: Sequence[str], names: Callable[[C], Iterable[str]]
) -> list[C]:
    """The cases `--only` names, by id or by any other name a case answers to."""
    if not only:
        return list(cases)
    known = {n for c in cases for n in names(c)}
    unknown = [o for o in only if o not in known]
    if unknown:
        raise CannotRun(f"no case named {', '.join(unknown)}")
    wanted = set(only)
    return [c for c in cases if wanted & set(names(c))]


def results_path(name: str, model: str, at: datetime) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return RESULTS / f"{name}-{safe}-{at.strftime('%Y%m%dT%H%M%S.%fZ')}.json"


class Report:
    """The judge first, one line per case as it finishes, then the totals and the verdict;
    and the same as JSON, UTF-8 with LF line endings on every system."""

    def __init__(
        self,
        name: str,
        model: str,
        judge: str,
        *,
        out: TextIO | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.name, self.model, self.judge = name, model, judge
        self._out = out if out is not None else sys.stdout
        self._now = now if now is not None else (lambda: datetime.now(UTC))
        self.started = self._now()
        self.cases: list[dict[str, Any]] = []
        self._say(judge)

    def _say(self, line: str) -> None:
        self._out.write(line + "\n")
        self._out.flush()

    def case(self, case_id: str, passed: bool, line: str, **data: Any) -> None:
        self.cases.append({"id": case_id, "passed": passed, **data})
        self._say(f"{'pass' if passed else 'FAIL'}  {case_id:<22} {line}")

    def finish(
        self,
        passed: bool,
        summary: str,
        *,
        notes: Sequence[str] = (),
        totals: Mapping[str, Any] | None = None,
        path: Path | None = None,
    ) -> int:
        for note in notes:
            self._say(note)
        self._say(f"{'PASS' if passed else 'FAIL'} ({self.model}): {summary}")
        target = path if path is not None else results_path(self.name, self.model, self.started)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "eval": self.name,
            "model": self.model,
            "judge": self.judge,
            "started": self.started.isoformat(),
            "finished": self._now().isoformat(),
            "verdict": "pass" if passed else "fail",
            "summary": summary,
            "totals": dict(totals or {}),
            "cases": self.cases,
        }
        with target.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
            f.write("\n")
        self._say(f"results: {target}")
        return PASS if passed else FAIL


JUDGE_SYSTEM = (
    "You grade one exchange with a personal assistant against the rubric you are given. "
    "Answer with JSON only, nothing before or after it: "
    '{"score": <a whole number from 1 to 5>, "reason": "<one short sentence>"}'
)
JUDGE_MAX_TOKENS = 2000


@dataclass(frozen=True, kw_only=True)
class Score:
    # 1 to 5, or None when the judge never gave a usable answer: a judge error, which is
    # reported and never counted as a pass.
    value: int | None
    reason: str

    @property
    def error(self) -> bool:
        return self.value is None


def _json(text: str) -> Any:
    """The JSON in a judge's answer: the whole answer, or else the one object in it,
    from its first brace to its last, so a fence or a line before it does no harm."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def parse_score(text: str) -> Score | None:
    """The judge's JSON, or None if it is not exactly a score from 1 to 5 and a reason.
    A whole number written as 4.0 is still 4."""
    body = _json(text.strip())
    if not isinstance(body, dict):
        return None
    value, reason = body.get("score"), body.get("reason")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        return None
    if not isinstance(reason, str):
        return None
    return Score(value=value, reason=reason.strip())


class Judge:
    """Scores a transcript against a rubric. Its own event loop for its whole life: a
    provider's connections belong to the loop that opened them."""

    def __init__(self, provider: ModelProvider, *, local: bool) -> None:
        self.provider = provider
        self.local = local
        self._runner = asyncio.Runner()

    @property
    def line(self) -> str:
        tail = " (local judge: scores are less reliable)" if self.local else ""
        return f"judge: {self.provider.model}{tail}"

    def score(self, rubric: str, transcript: str) -> Score:
        said = ""
        for _ in range(2):  # an answer that is not a score is asked for once more
            try:
                said = self._runner.run(self._ask(rubric, transcript))
            except ProviderError as e:
                said = f"(the judge failed: {e.kind})"
                continue
            found = parse_score(said)
            if found is not None:
                return found
        return Score(value=None, reason=f"judge error: {said[:200]!r}")

    async def _ask(self, rubric: str, transcript: str) -> str:
        asked = f"{rubric}\n\n<transcript>\n{transcript}\n</transcript>"
        text: list[str] = []
        async for ev in self.provider.stream(
            system=[SystemBlock(text=JUDGE_SYSTEM)],
            messages=[Msg(role="user", blocks=(TextBlock(text=asked),))],
            tools=[],
            max_tokens=JUDGE_MAX_TOKENS,
        ):
            if isinstance(ev, TextDelta):
                text.append(ev.text)
        return "".join(text)

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> Judge:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


Get = Callable[..., httpx.Response]


def reachable(url: str, *, get: Get = httpx.get) -> None:
    """Something answers at this OpenAI-compatible address, or the eval cannot run."""
    try:
        answer = get(f"{url.rstrip('/')}/models", timeout=3.0)
    except httpx.HTTPError as e:
        raise CannotRun(f"nothing answered at {url}: is Ollama running?") from e
    if answer.status_code >= 400:
        raise CannotRun(f"{url} answered {answer.status_code}")


def _local(model: str, url: str) -> ModelProvider:
    try:
        # An empty store held in memory: an eval never reads or writes the keychain.
        return config.build(ModelSettings(kind="custom", model=model, base_url=url), MemoryStore())
    except ConfigError as e:
        raise CannotRun(f"{url}: {e.code}") from e


def model_provider(
    model: str, url: str, env: Mapping[str, str], *, get: Get = httpx.get
) -> Callable[[], ModelProvider]:
    """How the sandbox makes the model under test, one per conversation. A `claude-`
    model needs the key in the environment; anything else is an OpenAI-compatible
    address that must answer now."""
    if model.startswith("claude-"):
        key = env.get(ANTHROPIC_KEY, "").strip()
        if not key:
            raise CannotRun(f"{model} needs {ANTHROPIC_KEY} set in the environment")
        return lambda: AnthropicProvider(api_key=lambda: key, model=model)
    reachable(url, get=get)
    _local(model, url)  # once now, so a bad address fails before anything starts
    return lambda: _local(model, url)


def judge_for(model: str, url: str, env: Mapping[str, str], *, get: Get = httpx.get) -> Judge:
    """Claude when the key is in the environment, else the local model. Never the keychain."""
    key = env.get(ANTHROPIC_KEY, "").strip()
    if key:
        return Judge(AnthropicProvider(api_key=lambda: key, model=DEFAULT_MODEL), local=False)
    reachable(url, get=get)
    return Judge(_local(model, url), local=True)


# ── Her, in a sandbox ────────────────────────────────────────────────────

Via = Literal["text", "voice"]
Tap = Literal["confirm", "cancel"]
USER, AGENT = "Sam", "Nova"
# A real model can think for a while between messages; a scripted one never needs it.
PATIENT_S = 300.0
ENDS = ("turn.complete", "error", "interrupted")
# Made-up microphone audio: half a second of "voice", then enough quiet for a batch
# recognizer to decide they stopped (tests/api/test_listening.py does the same). It is
# only ever sent as bytes over the socket: never played, never recorded.
SPOKEN = tone(0.5) + room(2.2)


@dataclass(frozen=True, kw_only=True)
class Ask:
    """One thing the user says. `tap` answers the card it raises, if it raises one."""

    text: str
    via: Via = "text"
    tap: Tap | None = None


@dataclass(frozen=True, kw_only=True)
class ToolRun:
    name: str
    ok: bool
    # How the page showed the call ending: succeeded, failed, confirmation_required.
    summary: str
    # What came back, as she was given it.
    result: str


@dataclass(frozen=True, kw_only=True)
class Turn:
    said: str
    via: Via
    reply: str
    tools: tuple[ToolRun, ...] = ()
    cards: tuple[str, ...] = ()
    tapped: Tap | None = None
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class Transcript:
    turns: tuple[Turn, ...]
    # Every tool result and host note she was given: what a reply is held to.
    evidence: str = ""
    user: str = USER
    agent: str = AGENT

    @property
    def replies(self) -> list[str]:
        return [t.reply for t in self.turns]

    def render(self) -> str:
        """The exchange as the judge reads it."""
        lines: list[str] = []
        for t in self.turns:
            lines.append(f"{self.user} ({'spoken' if t.via == 'voice' else 'typed'}): {t.said}")
            for r in t.tools:
                shown = " ".join(r.result.split())[:400]
                lines.append(f"  [tool {r.name}, {r.summary}] {shown}")
            for c in t.cards:
                lines.append(f"  [approval card: {c}]")
            if t.tapped is not None:
                tapped = "Approve" if t.tapped == "confirm" else "Decline"
                lines.append(f"  [{self.user} tapped {tapped}]")
            lines.append(f"{self.agent}: {t.reply}")
        return "\n".join(lines)


# How the host opens a note on a background read that ended (api/session.py).
_READ_ENDED = "The background read you started ("
# At most this many follow-up turns are waited for after one ask.
MAX_FOLLOW_UPS = 8


class Recorder:
    """The model under test, passed through, keeping what she was sent each turn.

    The attention runner, the scheduler and the widget refresher share her model, so
    the latest call is not always this conversation's: what a turn is held to comes
    from the latest call that carries the conversation's first message."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.calls: list[tuple[list[SystemBlock], list[Msg]]] = []
        # What the user said in this conversation, in order.
        self.said: list[str] = []

    def pricing(self) -> Pricing | None:
        return self.inner.pricing()

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        if not is_naming(system):  # naming a conversation is not a turn
            self.calls.append((list(system), list(messages)))
        async for ev in self.inner.stream(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        ):
            yield ev

    def _last(self) -> list[Msg]:
        if not self.said:
            return []
        first = self.said[0]
        for _, messages in reversed(self.calls):
            if any(
                m.role == "user" and isinstance(b, TextBlock) and b.text == first
                for m in messages
                for b in m.blocks
            ):
                return messages
        return []

    def reads_ended(self) -> int:
        """How many background reads she has been told ended, in this conversation."""
        return sum(
            1
            for m in self._last()
            for b in m.blocks
            if isinstance(b, TextBlock) and _READ_ENDED in b.text
        )

    def results(self) -> dict[str, str]:
        """Each tool call's result, by call id, as she was given it."""
        return {
            b.tool_use_id: b.content
            for m in self._last()
            for b in m.blocks
            if isinstance(b, ToolResultBlock)
        }

    def evidence(self) -> str:
        """Every tool result and host note in the conversation so far."""
        seen: list[str] = []
        for m in self._last():
            for b in m.blocks:
                if isinstance(b, ToolResultBlock):
                    seen.append(b.content)
                elif isinstance(b, TextBlock) and b.text.startswith("[host]"):
                    seen.append(b.text)
        return "\n".join(seen)


def _no_notification(title: str, body: str, *, silent: bool = False) -> bool:
    return False


def _finished(msgs: Sequence[dict[str, Any]]) -> bool:
    return bool(msgs) and msgs[-1]["type"] == "turn.complete"


class Sandbox:
    """Her, inside this process: a throwaway home, a keychain in memory, her real prompt,
    brain, session, spine and tools, over the same socket the page uses. Each case gets
    a fresh app, so nothing one case leaves (a memory, a file, a rule) reaches the next.
    Nothing reaches the user's data or a running Trillion, and nothing is played aloud."""

    def __init__(
        self,
        provider: Callable[[], ModelProvider],
        *,
        root: Path | None = None,
        patient: bool = False,
    ) -> None:
        self._make = provider
        self._given_root = root
        # The folder this sandbox made, and the only one it ever deletes.
        self._made: Path | None = None
        self._patient = patient
        self._stack = ExitStack()
        self._chat: Chat | None = None
        self._app: App | None = None
        self._count = 0
        self._saved: dict[str, Any] = {}
        self.root: Path | None = None
        self.provider: Recorder
        self.stt: FakeSTT

    def __enter__(self) -> Sandbox:
        if self._given_root is None:
            self._made = Path(tempfile.mkdtemp(prefix="trillion-eval-"))
            self.root = self._made
        else:
            # A folder of its own under the given root, which is kept for a look after:
            # two sandboxes on one root never meet.
            self._given_root.mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix="sandbox-", dir=self._given_root))
        self._saved = {
            "home": os.environ.get("TRILLION_HOME"),
            "timeout": test_server.RECEIVE_TIMEOUT_S,
            "mic": quiet.SYSTEM_MIC,
            "focus": quiet.SYSTEM_FOCUS,
        }
        # Never this computer's microphone or Focus, as in the tests (tests/conftest.py).
        quiet.SYSTEM_MIC = lambda host: None
        quiet.SYSTEM_FOCUS = lambda: None
        if self._patient:
            test_server.RECEIVE_TIMEOUT_S = PATIENT_S
        try:
            self._open()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._close()
        finally:
            # Put back only what an open sandbox changed: closing one never opened, or
            # closing twice, leaves the environment as it found it.
            saved, self._saved = self._saved, {}
            if saved:
                home = saved["home"]
                if home is None:
                    os.environ.pop("TRILLION_HOME", None)
                else:
                    os.environ["TRILLION_HOME"] = home
                test_server.RECEIVE_TIMEOUT_S = saved["timeout"]
                quiet.SYSTEM_MIC = saved["mic"]
                quiet.SYSTEM_FOCUS = saved["focus"]
            self.root = None
            made, self._made = self._made, None
            if made is not None:
                shutil.rmtree(made, ignore_errors=True)

    @property
    def app(self) -> App:
        if self._app is None:
            raise RuntimeError("the sandbox is not open")
        return self._app

    def fresh(self) -> None:
        """A new conversation in a new app and home: cases never see each other."""
        self._close()
        self._open()

    def _open(self) -> None:
        if self.root is None:
            raise RuntimeError("the sandbox is not open")
        self._count += 1
        place = self.root / f"case-{self._count}"
        place.mkdir(parents=True)
        os.environ["TRILLION_HOME"] = str(place / "home")
        self.provider = Recorder(self._make())
        app = App(place, self.provider)
        self._app = app
        rt = app.rt
        # No voice: nothing she says is turned into sound.
        rt.tts, rt.speech = None, None
        # No system notification: a reminder or a background read never reaches this
        # computer's notification centre.
        if rt.door is not None:
            rt.door.notify = _no_notification
        # A scripted recognizer: a spoken turn hears exactly the words the eval gives it.
        self.stt = FakeSTT(streaming=False)
        rt.stt, rt.listening = self.stt, ListenSettings(kind="deepgram", choice="en")
        named = app.client.put(
            "/api/settings/persona",
            json={"user_name": USER, "agent_name": AGENT},
            headers=app.auth,
        )
        if named.status_code != 200:
            raise CannotRun(f"could not tell her who she is: {named.status_code}")

    def _close(self) -> None:
        self._stack.close()
        self._stack = ExitStack()
        self._chat = None
        if self._app is not None:
            app, self._app = self._app, None
            app.close()

    def _conversation(self) -> Chat:
        if self._chat is None:
            self._chat = self._stack.enter_context(self.app.connect())
        return self._chat

    def converse(self, asks: Sequence[Ask]) -> Transcript:
        """Each ask in turn, in this case's conversation."""
        turns = tuple(self._take(ask) for ask in asks)
        return Transcript(turns=turns, evidence=self.provider.evidence())

    def _take(self, ask: Ask) -> Turn:
        chat = self._conversation()
        self.provider.said.append(ask.text)
        ended_before = self.provider.reads_ended()
        msgs = self._speak(chat, ask.text) if ask.via == "voice" else chat.say(ask.text)
        answer = reply(msgs)
        cards = [m for m in msgs if m["type"] == "confirm.request"]
        tapped: Tap | None = None
        if _finished(msgs):
            chat.until("state")  # her turn is over: the session is at rest again
            if ask.tap is not None and cards:
                msgs.append(chat.tap(ask.tap, str(cards[-1]["pending_id"])))
                after = chat.until(*ENDS)
                msgs += after
                answer = " ".join(s for s in (answer, reply(after)) if s)
                tapped = ask.tap
                if _finished(after):
                    chat.until("state")
            # A read that went to the background (web_fetch, a connected app) comes back
            # in a turn of its own once it ends, one read or several to a turn: wait for
            # every one, as the user would hear them, and for any read a follow-up starts.
            for _ in range(MAX_FOLLOW_UPS):
                started = sum(
                    m["type"] == "tool.end" and m.get("summary") == "background" for m in msgs
                )
                if not _finished(msgs) or self.provider.reads_ended() - ended_before >= started:
                    break
                later = chat.until(*ENDS)
                msgs += later
                answer = " ".join(s for s in (answer, reply(later)) if s)
                if _finished(later):
                    chat.until("state")
        return self._turn(ask, msgs, answer, tapped)

    def _speak(self, chat: Chat, text: str) -> list[dict[str, Any]]:
        """A spoken turn: the page opens the microphone, sends audio, and the scripted
        recognizer hears `text` once the audio stops. The session marks it spoken."""
        self.stt.on_finish = [text]
        chat.ws.send_json({"type": "listen.start"})
        opened = chat.until("mic.start", "error")
        if opened[-1]["type"] == "error":
            return opened
        for frame in frames(SPOKEN):
            chat.ws.send_bytes(frame)
        return opened + chat.until(*ENDS)

    def _turn(self, ask: Ask, msgs: list[dict[str, Any]], answer: str, tapped: Tap | None) -> Turn:
        results = self.provider.results()
        ended = {m["call_id"]: m for m in msgs if m["type"] == "tool.end"}
        tools = tuple(
            ToolRun(
                name=str(m["name"]),
                ok=bool(ended.get(m["call_id"], {}).get("ok", False)),
                summary=str(ended.get(m["call_id"], {}).get("summary", "never ended")),
                result=results.get(str(m["call_id"]), ""),
            )
            for m in msgs
            if m["type"] == "tool.start"
        )
        heard = [str(m["text"]) for m in msgs if m["type"] == "transcript.final"]
        errors = [str(m.get("code", "error")) for m in msgs if m["type"] == "error"]
        return Turn(
            said=heard[-1] if heard else ask.text,
            via=ask.via,
            reply=answer,
            tools=tools,
            cards=tuple(str(m["summary"]) for m in msgs if m["type"] == "confirm.request"),
            tapped=tapped,
            error=errors[-1] if errors else None,
        )
