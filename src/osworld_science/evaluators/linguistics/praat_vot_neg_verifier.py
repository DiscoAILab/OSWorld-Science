#!/usr/bin/env python3
"""Semantic, dependency-free grader for the data_raw_dir1_1 negative-VOT task.

This token has a NEGATIVE voice onset time (prevoiced /b/).  The VOT interval is
stored in chronological order as [voicing_onset, release], so the interval's
``start`` is the voicing onset and its ``end`` is the release burst.  The signed
VOT is therefore ``start - end`` (negative); the interval duration is
``end - start`` (positive) and equals the |VOT| magnitude used for tolerance.

Scores 0.25 per target word with inclusive start, end, AND duration tolerances.
Global structural violations score zero. A missing or duplicate annotation
attributable to one word fails that word only.  No reference data is needed
inside the agent's desktop environment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Tuple

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_INITIAL = PACKAGE_DIR / "assets" / "data_raw_dir1_1.TextGrid"
DEFAULT_REFERENCE = PACKAGE_DIR / "private" / "data_raw_dir1_1_reference.TextGrid"
TOLERANCES_SECONDS = {"bun": 0.005}
# This is a negative-VOT (prevoiced) token.  The VOT interval is stored
# chronologically as [voicing_onset, release]; therefore the signed VOT is
# ``start - end`` (negative) and the tolerance quantity is the interval
# duration ``end - start`` (positive).  The provided anchor is the word-start
# boundary (the voicing onset), which the reference VOT interval must retain.
VOT_SIGN = "negative"
STRUCTURE_EPSILON = 1e-6
COMPARISON_EPSILON = 1e-12  # Only binary floating-point rounding, not extra tolerance.
MAX_FILE_BYTES = 2_000_000
MAX_ITEMS = 100_000


class TextGridError(ValueError):
    """A submission cannot be interpreted as a supported, valid TextGrid."""


class ConfigurationError(ValueError):
    """Initial/reference data are missing or invalid; this is not agent failure."""


@dataclass(frozen=True)
class Interval:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Tier:
    kind: str
    name: str
    start: float
    end: float
    intervals: Tuple[Interval, ...]


@dataclass(frozen=True)
class TextGrid:
    start: float
    end: float
    tiers: Tuple[Tier, ...]


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int


def _lex(source: str) -> List[Token]:
    """Lex strings with Praat's doubled quotes; never inspect inside labels.

    Field names, punctuation and numbers remain separate tokens, so interval
    indices cannot be mistaken for time values. Praat ! comments are ignored.
    """
    tokens = []
    i, line = 0, 1
    while i < len(source):
        char = source[i]
        if char.isspace():
            line += char == "\n"
            i += 1
        elif char == "!":
            while i < len(source) and source[i] != "\n":
                i += 1
        elif char == '"':
            start_line = line
            i += 1
            label = []
            while i < len(source):
                if source[i] == '"':
                    if i + 1 < len(source) and source[i + 1] == '"':
                        label.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                line += source[i] == "\n"
                label.append(source[i])
                i += 1
            else:
                raise TextGridError("Unterminated quoted string at line %d" % start_line)
            tokens.append(Token("string", "".join(label), start_line))
        elif char in "[]=?:<>":
            tokens.append(Token("symbol", char, line))
            i += 1
        else:
            start = i
            while (i < len(source) and not source[i].isspace()
                   and source[i] not in '"![]=?:<>'):
                i += 1
            tokens.append(Token("bare", source[start:i], line))
    return tokens


class _Parser:
    def __init__(self, source: str):
        self.tokens = _lex(source)
        self.pos = 0

    def peek(self, value: str) -> bool:
        return self.pos < len(self.tokens) and self.tokens[self.pos].value == value

    def take(self) -> Token:
        if self.pos >= len(self.tokens):
            raise TextGridError("Unexpected end of TextGrid")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, value: str) -> None:
        token = self.take()
        if token.value != value or token.kind == "string":
            raise TextGridError("Expected %r at line %d, got %r" %
                                (value, token.line, token.value))

    def string(self) -> str:
        token = self.take()
        if token.kind != "string":
            raise TextGridError("Expected a quoted string at line %d" % token.line)
        return token.value

    def number(self) -> float:
        token = self.take()
        if (token.kind != "bare" or
                not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", token.value)):
            raise TextGridError("Invalid finite number %r at line %d" % (token.value, token.line))
        value = float(token.value)
        if not math.isfinite(value):
            raise TextGridError("Nonfinite time/count at line %d" % token.line)
        return value

    def count(self) -> int:
        value = self.number()
        if not value.is_integer() or not 0 <= value <= MAX_ITEMS:
            raise TextGridError("Invalid item count/index: %r" % value)
        return int(value)

    def field(self, label: str, method):
        self.expect(label)
        self.expect("=")
        return method()

    def indexed(self, label: str, index: Optional[int]) -> None:
        self.expect(label)
        self.expect("[")
        if index is not None and self.count() != index:
            raise TextGridError("%s indices must be consecutive starting at 1" % label)
        self.expect("]")
        self.expect(":")

    def parse(self) -> TextGrid:
        self.expect("File")
        self.expect("type")
        self.expect("=")
        file_type = self.string()
        if file_type not in ("ooTextFile", "ooTextFile short"):
            raise TextGridError("Only standard long/short text TextGrid files are supported")
        if self.peek("Object"):
            self.expect("Object")
            object_class = self.field("class", self.string)
        else:
            object_class = self.string()
        if object_class != "TextGrid":
            raise TextGridError("Object class must be TextGrid")
        long_format = self.peek("xmin")
        start = self.field("xmin", self.number) if long_format else self.number()
        end = self.field("xmax", self.number) if long_format else self.number()
        if long_format:
            self.expect("tiers")
            self.expect("?")
        self.expect("<")
        self.expect("exists")
        self.expect(">")
        count = self.field("size", self.count) if long_format else self.count()
        if long_format:
            self.indexed("item", None)
        tiers = []
        for i in range(1, count + 1):
            if long_format:
                self.indexed("item", i)
                kind = self.field("class", self.string)
                name = self.field("name", self.string)
                tier_start = self.field("xmin", self.number)
                tier_end = self.field("xmax", self.number)
            else:
                kind, name = self.string(), self.string()
                tier_start, tier_end = self.number(), self.number()
            if kind not in ("IntervalTier", "TextTier"):
                raise TextGridError("Unsupported tier class: %s" % kind)
            collection = "intervals" if kind == "IntervalTier" else "points"
            if long_format:
                self.expect(collection)
                self.expect(":")
                items_count = self.field("size", self.count)
            else:
                items_count = self.count()
            intervals = []
            for j in range(1, items_count + 1):
                if long_format:
                    self.indexed(collection, j)
                    item_start = self.field("xmin" if kind == "IntervalTier" else "number", self.number)
                    item_end = self.field("xmax", self.number) if kind == "IntervalTier" else item_start
                    label = self.field("text" if kind == "IntervalTier" else "mark", self.string)
                else:
                    item_start = self.number()
                    item_end = self.number() if kind == "IntervalTier" else item_start
                    label = self.string()
                intervals.append(Interval(item_start, item_end, label))
            tiers.append(Tier(kind, name, tier_start, tier_end, tuple(intervals)))
        if self.pos != len(self.tokens):
            raise TextGridError("Unexpected trailing content at line %d" % self.tokens[self.pos].line)
        return TextGrid(start, end, tuple(tiers))


def read_textgrid(path: Path) -> TextGrid:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise TextGridError("TextGrid exceeds %d bytes" % MAX_FILE_BYTES)
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            source = data.decode("utf-16")
        else:
            source = data.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise TextGridError("Cannot read TextGrid: %s" % exc) from exc
    return _Parser(source).parse()


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= STRUCTURE_EPSILON + COMPARISON_EPSILON


def _validate_structure(grid: TextGrid) -> Dict[str, Tier]:
    if grid.start < 0 or grid.end <= grid.start:
        raise TextGridError("Invalid TextGrid time domain")
    names = [tier.name for tier in grid.tiers]
    if len(names) != 2 or set(names) != {"word", "VOT"}:
        raise TextGridError("Exactly one word tier and one VOT tier are required; extra/duplicate tiers are invalid")
    for tier in grid.tiers:
        if tier.kind != "IntervalTier":
            raise TextGridError("%s must be an IntervalTier" % tier.name)
        if not (_close(tier.start, grid.start) and _close(tier.end, grid.end)):
            raise TextGridError("%s tier domain differs from the TextGrid domain" % tier.name)
        if not tier.intervals:
            raise TextGridError("%s tier has no intervals" % tier.name)
        cursor = tier.start
        for interval in tier.intervals:
            if interval.end <= interval.start:
                raise TextGridError("%s tier has an empty or reversed interval" % tier.name)
            if (interval.start < tier.start - STRUCTURE_EPSILON or
                    interval.end > tier.end + STRUCTURE_EPSILON):
                raise TextGridError("%s tier contains an out-of-domain interval" % tier.name)
            if not _close(interval.start, cursor):
                raise TextGridError("%s intervals must be ordered, contiguous and nonoverlapping" % tier.name)
            cursor = interval.end
        if not _close(cursor, tier.end):
            raise TextGridError("%s intervals do not cover the tier domain" % tier.name)
    return {tier.name: tier for tier in grid.tiers}


def _preserves_initial(grid: TextGrid, tiers: Dict[str, Tier],
                       initial: TextGrid, initial_tiers: Dict[str, Tier]) -> None:
    if not (_close(grid.start, initial.start) and _close(grid.end, initial.end)):
        raise TextGridError("TextGrid time domain changed from the initial file")
    actual, expected = tiers["word"].intervals, initial_tiers["word"].intervals
    if len(actual) != len(expected):
        raise TextGridError("word tier interval count changed")
    for a, b in zip(actual, expected):
        if a.text != b.text or not (_close(a.start, b.start) and _close(a.end, b.end)):
            raise TextGridError("word tier labels or boundaries changed")


def _match_annotations(tier: Tier, words: Dict[str, Interval]):
    matches = {name: [] for name in TOLERANCES_SECONDS}
    errors = []
    for interval in tier.intervals:
        label = interval.text.strip()
        if not label:
            continue
        if label != "VOT":
            errors.append("Unexpected VOT-tier label: %r" % interval.text)
            continue
        candidates = []
        for name, word in words.items():
            tolerance = TOLERANCES_SECONDS[name]
            # Use overlap with the expanded word domain, not strict containment.
            # A start at reference minus its tolerance must remain matchable.
            if (interval.end > word.start - tolerance - COMPARISON_EPSILON and
                    interval.start < word.end + tolerance + COMPARISON_EPSILON):
                candidates.append(name)
        if len(candidates) != 1:
            errors.append("Labeled interval %.9f–%.9f maps to %d target words" %
                          (interval.start, interval.end, len(candidates)))
        else:
            matches[candidates[0]].append(interval)
    return matches, errors


def _values(interval: Interval) -> dict:
    """Report both the physical interval and the signed (negative) VOT value.

    ``start`` is the voicing onset, ``end`` the release; the signed VOT is thus
    ``start - end`` and the tolerance quantity is the (positive) duration.
    """
    duration = interval.end - interval.start
    return {"start_seconds": interval.start, "end_seconds": interval.end,
            "duration_seconds": duration, "duration_ms": duration * 1000,
            "signed_vot_seconds": interval.start - interval.end,
            "signed_vot_ms": (interval.start - interval.end) * 1000}


def load_configuration(initial: Path = DEFAULT_INITIAL,
                       reference: Path = DEFAULT_REFERENCE) -> dict:
    try:
        starter = read_textgrid(initial)
        initial_tiers = _validate_structure(starter)
        words = {}
        for interval in initial_tiers["word"].intervals:
            if interval.text:
                if interval.text not in TOLERANCES_SECONDS or interval.text in words:
                    raise TextGridError("Initial word tier must contain bun exactly once")
                words[interval.text] = interval
        if set(words) != set(TOLERANCES_SECONDS):
            raise TextGridError("Initial word tier must contain bun exactly once")
        if any(interval.text.strip() for interval in initial_tiers["VOT"].intervals):
            raise TextGridError("Initial VOT tier must have blank labels")
        answer = read_textgrid(reference)
        reference_tiers = _validate_structure(answer)
        _preserves_initial(answer, reference_tiers, starter, initial_tiers)
        matches, errors = _match_annotations(reference_tiers["VOT"], words)
        if errors or any(len(matches[name]) != 1 for name in TOLERANCES_SECONDS):
            raise TextGridError("Reference requires exactly one VOT interval per target word; " + "; ".join(errors))
        for name, intervals in matches.items():
            annotation, word = intervals[0], words[name]
            if (annotation.start < word.start - STRUCTURE_EPSILON or
                    annotation.end > word.end + STRUCTURE_EPSILON):
                raise TextGridError("Reference VOT must be contained in its target word: %s" % name)
            # The task provides the word-start boundary as the voicing onset and
            # requires it to be retained, so the reference VOT must begin there.
            # This also catches a reference drawn in the reversed orientation.
            if not _close(annotation.start, word.start):
                raise TextGridError(
                    "Reference VOT for %s must start at the provided word-start "
                    "boundary (voicing onset); signed VOT would be %.3f ms" %
                    (name, (annotation.start - annotation.end) * 1000))
    except TextGridError as exc:
        raise ConfigurationError(str(exc)) from exc
    return {"initial": starter, "initial_tiers": initial_tiers, "words": words,
            "reference": {name: matches[name][0] for name in TOLERANCES_SECONDS}}


def evaluate_submission(submission: Path, reference: Path = DEFAULT_REFERENCE,
                        initial: Path = DEFAULT_INITIAL) -> dict:
    """Return JSON-compatible feedback; raise ConfigurationError for bad gold data."""
    config = load_configuration(initial, reference)
    report = {"status": "evaluated", "score": 0.0, "success": False,
              "valid_submission": False, "passed_tokens": 0,
              "total_tokens": len(TOLERANCES_SECONDS),
              "global_errors": [], "tokens": [],
              "policy": {"vot_sign": "negative",
                         "interval_convention": "[voicing_onset, release]; signed VOT = start - end",
                         "comparison": "inclusive start, end and duration absolute errors",
                         "tolerance_ms": {name: value * 1000 for name, value in TOLERANCES_SECONDS.items()},
                         "word_tier_epsilon_seconds": STRUCTURE_EPSILON,
                         "global_violation_score": 0}}
    matches = {name: [] for name in TOLERANCES_SECONDS}
    try:
        grid = read_textgrid(submission)
        tiers = _validate_structure(grid)
        _preserves_initial(grid, tiers, config["initial"], config["initial_tiers"])
        matches, errors = _match_annotations(tiers["VOT"], config["words"])
        report["global_errors"].extend(errors)
    except TextGridError as exc:
        report["global_errors"].append(str(exc))
    report["valid_submission"] = not report["global_errors"]
    for name, tolerance in TOLERANCES_SECONDS.items():
        expected = config["reference"][name]
        candidates = matches[name]
        token = {"word": name, "tolerance_ms": tolerance * 1000,
                 "reference": _values(expected), "submitted": None,
                 "candidate_count": len(candidates), "errors_ms": None,
                 "passed": False, "reasons": []}
        if not candidates:
            token["reasons"].append("Missing VOT annotation")
        elif len(candidates) > 1:
            token["submitted_candidates"] = [_values(item) for item in candidates]
            token["reasons"].append("Duplicate VOT annotations for this word")
        else:
            actual = candidates[0]
            errors = {"start": abs(actual.start - expected.start),
                      "end": abs(actual.end - expected.end),
                      "duration": abs((actual.end - actual.start) - (expected.end - expected.start))}
            token["submitted"] = _values(actual)
            # Signed-VOT error is reported for negative-VOT transparency; it is
            # numerically identical to the duration error and is not a separate
            # pass/fail criterion.
            token["signed_vot_error_ms"] = abs(
                (actual.start - actual.end) - (expected.start - expected.end)) * 1000
            token["errors_ms"] = {key: value * 1000 for key, value in errors.items()}
            for key, value in errors.items():
                if value > tolerance + COMPARISON_EPSILON:
                    token["reasons"].append("%s error %.6f ms exceeds %.3f ms" %
                                            (key, value * 1000, tolerance * 1000))
        if report["global_errors"]:
            token["reasons"].append("Global submission validation failed")
        token["passed"] = not token["reasons"]
        report["tokens"].append(token)
    report["passed_tokens"] = sum(token["passed"] for token in report["tokens"])
    report["score"] = report["passed_tokens"] / report["total_tokens"]
    report["success"] = report["passed_tokens"] == report["total_tokens"]
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--initial", type=Path, default=DEFAULT_INITIAL)
    parser.add_argument("--report", type=Path, help="Also save JSON feedback to this host-side path")
    parser.add_argument("--check-reference", action="store_true", help="Validate grader fixtures without a submission")
    args = parser.parse_args(argv)
    if not args.check_reference and args.submission is None:
        parser.error("--submission is required unless --check-reference is used")
    code = 0
    try:
        if args.check_reference:
            config = load_configuration(args.initial, args.reference)
            result = {"status": "configuration_valid", "reference_tokens": {
                name: dict(_values(item), tolerance_ms=TOLERANCES_SECONDS[name] * 1000)
                for name, item in config["reference"].items()}}
        else:
            result = evaluate_submission(args.submission, args.reference, args.initial)
    except ConfigurationError as exc:
        result = {"status": "configuration_error", "score": None, "success": False,
                  "global_errors": [str(exc)]}
        code = 2
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output, encoding="utf-8")
        except OSError as exc:
            print("Could not write report: %s" % exc, file=sys.stderr)
            code = 2
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    sys.exit(main())
