"""The screenshot window every agent applies to what it sends the model.

One setting, the same in every configs/agents/<agent>.yaml:

    screenshot_window:
      limit: 6            # the most screenshots one request carries, the newest included
      keep: 5             # what a prune leaves: this many earlier screenshots
      prune: drop_oldest  # the algorithm that chooses them (register_prune)

Screenshots accumulate in the window. When it holds `limit` and a new one arrives,
the prune algorithm first cuts the earlier screenshots down to `keep`, then the new
one joins: once the window has filled, a request carries between keep + 1 and
`limit` screenshots. The newest screenshot is never pruned, and a pruned one never
comes back. `keep = limit - 1` is a sliding window (one screenshot leaves per step:
the recorded sweeps' 5 + current); a gap between them makes pruning rare, which
keeps the request prefix byte-stable for prompt caching between prunes (what
Anthropic's reference loop calls interval pruning). `limit: null` (or
`screenshot_window: null`) never prunes.

The default algorithm, `drop_oldest`, drops the oldest `limit - keep` screenshots.
Other algorithms are registered with `@register_prune("name")`, or named in the
config as `package.module:function`. An algorithm receives the earlier screenshots
(everything held except the newest, oldest first) and `keep`, and returns the ones to
keep: a subset of what it was given, at most `keep` of them.

What "pruned" means is the agent's business: the PromptAgent drops the whole earlier
turn (screenshot and reply), the computer-use agents replace the image with a short
text or drop the call, the Kimi adapter writes Moonshot's own "[Screenshot at Step n]".
"""
from __future__ import annotations

import importlib
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Screenshot:
    """One screenshot as a prune algorithm sees it."""
    id: Hashable          # the agent's own handle (a step number, a call id, a message position)
    ordinal: int          # 0-based position among every screenshot of the run, in arrival order


PruneFn = Callable[[list[Screenshot], int], list[Screenshot]]
_PRUNERS: dict[str, PruneFn] = {}


def register_prune(name: str):
    """Decorator registering a prune algorithm under `name`."""
    def deco(fn: PruneFn) -> PruneFn:
        _PRUNERS[name] = fn
        return fn
    return deco


def prune_names() -> list[str]:
    return list(_PRUNERS)


def get_prune(name: str) -> PruneFn:
    """A registered algorithm, or `package.module:function` imported on first use."""
    if name in _PRUNERS:
        return _PRUNERS[name]
    if ":" in name:
        module, _, attr = name.partition(":")
        try:
            fn = getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"screenshot_window.prune {name!r} cannot be imported: {e}") from None
        if not callable(fn):
            raise ValueError(f"screenshot_window.prune {name!r} is not callable")
        _PRUNERS[name] = fn
        return fn
    raise ValueError(f"unknown screenshot_window.prune {name!r}; registered: {', '.join(_PRUNERS)} "
                     f"(or name one as package.module:function)")


@register_prune("drop_oldest")
def drop_oldest(earlier: list[Screenshot], keep: int) -> list[Screenshot]:
    """Keep the newest `keep` of the earlier screenshots (drops the oldest limit - keep)."""
    return list(earlier[len(earlier) - keep:]) if keep > 0 else []


_KEYS = frozenset({"limit", "keep", "prune"})


@dataclass(frozen=True)
class WindowConfig:
    limit: int | None = None      # None = never prune
    keep: int = 0
    prune: str = "drop_oldest"

    @classmethod
    def sliding(cls, history: int | None) -> WindowConfig:
        """`history` earlier screenshots plus the current one, one leaving per step (the
        old `history_window: N`)."""
        return cls() if history is None else cls(limit=int(history) + 1, keep=int(history))

    @classmethod
    def from_value(cls, value: Any, where: str = "screenshot_window") -> WindowConfig:
        """Validate a `screenshot_window` mapping (or None = unbounded). `keep` defaults
        to limit - 1, `prune` to drop_oldest."""
        if value is None:
            return cls()
        if isinstance(value, WindowConfig):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"{where} must be a mapping {{limit, keep, prune}} or null, not {value!r}")
        unknown = sorted(set(value) - _KEYS)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}; allowed: limit, keep, prune")
        limit = value.get("limit")
        prune = str(value.get("prune") or "drop_oldest")
        get_prune(prune)                                     # fail at load time, not at step 300
        if limit is None:
            return cls(prune=prune)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"{where}.limit must be a positive integer or null, not {limit!r}")
        keep = value.get("keep", limit - 1)
        if not isinstance(keep, int) or isinstance(keep, bool) or not 0 <= keep < limit:
            raise ValueError(f"{where}.keep must be an integer with 0 <= keep < limit ({limit}), not {keep!r}")
        return cls(limit=limit, keep=keep, prune=prune)

    @classmethod
    def from_options(cls, options: dict, default: Any = None) -> WindowConfig:
        """The window an agent factory uses: `screenshot_window` from its options (config
        file plus command-line overrides), else `default`."""
        if "history_window" in options:
            raise ValueError("history_window was replaced by screenshot_window: {limit, keep, prune}; "
                             "history_window: N is limit: N + 1, keep: N (or --history-window N)")
        return cls.from_value(options.get("screenshot_window", default))

    def describe(self) -> dict | None:
        return None if self.limit is None else {"limit": self.limit, "keep": self.keep, "prune": self.prune}


class ScreenshotWindow:
    """One run's window state: which screenshots have been pruned so far."""

    def __init__(self, config: WindowConfig | None = None):
        self.config = config or WindowConfig()
        self._fn = get_prune(self.config.prune)
        self.pruned: set[Hashable] = set()
        self._ordinals: dict[Hashable, int] = {}
        self.n_prunes = 0              # times the algorithm ran
        self.n_pruned = 0              # screenshots it removed, over the run

    def reset(self) -> None:
        self.pruned.clear()
        self._ordinals.clear()
        self.n_prunes = self.n_pruned = 0

    def _register(self, ids: Sequence[Hashable]) -> None:
        for i in ids:
            if i not in self._ordinals:
                self._ordinals[i] = len(self._ordinals)

    def apply(self, ids: Sequence[Hashable]) -> set[Hashable]:
        """`ids`: every screenshot of the request being built, oldest first, the newest
        last (ones pruned earlier may be listed or not). Prunes when the request would
        carry more than `limit`, and returns the ids to leave out."""
        self._register(ids)
        live = [i for i in ids if i not in self.pruned]
        if self.config.limit is not None and len(live) > self.config.limit:
            self._prune(live[:-1], self.config.keep)
        return self.pruned

    def force(self, ids: Sequence[Hashable], keep: int) -> set[Hashable]:
        """Prune now to `keep` earlier screenshots whatever the limit (an agent's size cap)."""
        self._register(ids)
        live = [i for i in ids if i not in self.pruned]
        if len(live) - 1 > keep:
            self._prune(live[:-1], keep)
        return self.pruned

    def _prune(self, earlier: list[Hashable], keep: int) -> None:
        shots = [Screenshot(i, self._ordinals[i]) for i in earlier]
        kept = self._fn(shots, keep)
        kept_ids = {s.id if isinstance(s, Screenshot) else s for s in kept}
        if not kept_ids <= set(earlier) or len(kept_ids) > keep:
            raise ValueError(f"prune algorithm {self.config.prune!r} must return at most {keep} of the "
                             f"screenshots it was given; it returned {len(kept_ids)}")
        dropped = [i for i in earlier if i not in kept_ids]
        self.pruned.update(dropped)
        self.n_prunes += 1
        self.n_pruned += len(dropped)

    def stats(self) -> dict:
        return {"screenshot_window": self.config.describe(), "screenshot_prunes": self.n_prunes,
                "screenshots_pruned": self.n_pruned}
