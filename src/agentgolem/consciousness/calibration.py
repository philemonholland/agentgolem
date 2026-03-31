"""Helpers for parsing and applying VowOS calibration reflections."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from agentgolem.consciousness.internal_state import InternalState
from agentgolem.consciousness.self_model import SelfModel

_VOW_ORDER = {
    "Purpose": 1,
    "Method": 2,
    "Conduct": 3,
    "Integrity": 4,
    "Evolution": 5,
}
_SECTION_TITLES = {
    "five vow review & assessment",
    "vow alignment assessment",
    "identified drift & imbalance",
    "identified drift, imbalance, or failure modes",
    "correction & intention for next cycle",
    "specific correction or intention for the next cycle",
    "affirmation of commitment",
    "affirmation",
}
_DRIFT_TITLES = {
    "identified drift & imbalance",
    "identified drift, imbalance, or failure modes",
}
_CORRECTION_TITLES = {
    "correction & intention for next cycle",
    "specific correction or intention for the next cycle",
}
_COMMITMENT_TITLES = {"affirmation of commitment", "affirmation"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_inline(text: str, limit: int) -> str:
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*")
    cleaned = re.sub(r"^[A-Za-z][A-Za-z \-/&]{0,40}:\s*", "", cleaned)
    return cleaned[:limit].rstrip(" ,.;:") if cleaned else ""


def _unique(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in items:
        cleaned = _clean_inline(item, 220)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
        if len(merged) >= limit:
            break
    return merged


def _normalize_heading(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^#+\s*", "", cleaned)
    cleaned = cleaned.strip("* ")
    cleaned = re.sub(r"^[\-\*]\s*", "", cleaned)
    cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
    cleaned = cleaned.strip("* ")
    cleaned = cleaned.rstrip(":")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _extract_section(text: str, titles: set[str]) -> str:
    lines = text.splitlines()
    body: list[str] = []
    capturing = False
    for line in lines:
        heading = _normalize_heading(line)
        if not capturing and heading in titles:
            capturing = True
            continue
        if not capturing:
            continue
        if line.strip().startswith("--- END"):
            break
        if heading.startswith("id"):
            break
        if heading in _SECTION_TITLES and heading not in titles:
            break
        body.append(line.rstrip())
    if body:
        return "\n".join(body).strip()
    return ""


def _extract_inline_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = re.compile(
            rf"{re.escape(label)}\s*:\s*(.+?)(?=\n\s*\n|\n\s*##\s*Id\b|\n\s*(?:##|###|\*\*\d+\.|--- END)|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            return _clean_inline(match.group(1), 320)
    return ""


def _split_highlights(text: str, limit: int) -> list[str]:
    if not text:
        return []
    bullet_lines = [
        _clean_inline(line, 220)
        for line in text.splitlines()
        if line.strip().startswith(("-", "*"))
    ]
    if bullet_lines:
        return _unique(bullet_lines, limit)
    fragments = re.split(r"(?:\n{2,}|(?<=[.!?])\s+)", text)
    return _unique(fragments, limit)


@dataclass
class CalibrationSummary:
    """Structured meaning extracted from a calibration reflection."""

    vow_scores: dict[str, float] = field(default_factory=dict)
    drift_signals: list[str] = field(default_factory=list)
    correction: str = ""
    commitment: str = ""
    excerpt: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CalibrationSummary:
        raw_scores = data.get("vow_scores", {})
        if isinstance(raw_scores, dict):
            vow_scores = {
                str(name): _clamp(float(score), 0.0, 10.0)
                for name, score in raw_scores.items()
            }
        else:
            vow_scores = {}
        drift_signals = (
            [str(item) for item in data.get("drift_signals", [])[:4]]
            if isinstance(data.get("drift_signals"), list)
            else []
        )
        return cls(
            vow_scores=vow_scores,
            drift_signals=drift_signals,
            correction=str(data.get("correction", "")),
            commitment=str(data.get("commitment", "")),
            excerpt=str(data.get("excerpt", "")),
        )

    def has_signal(self) -> bool:
        return bool(self.vow_scores or self.drift_signals or self.correction or self.commitment)

    def mean_alignment(self) -> float | None:
        if not self.vow_scores:
            return None
        return sum(self.vow_scores.values()) / len(self.vow_scores)

    def strongest_vows(self, limit: int = 2) -> list[tuple[str, float]]:
        ordered = sorted(
            self.vow_scores.items(),
            key=lambda item: (-item[1], _VOW_ORDER.get(item[0], 99)),
        )
        return ordered[:limit]

    def weakest_vows(self, limit: int = 2) -> list[tuple[str, float]]:
        ordered = sorted(
            self.vow_scores.items(),
            key=lambda item: (item[1], _VOW_ORDER.get(item[0], 99)),
        )
        return ordered[:limit]

    def prompt_injection(self) -> str:
        """Return a concise natural-language guidance block for prompts."""
        parts: list[str] = []
        strongest = self.strongest_vows(limit=2)
        if strongest:
            rendered = ", ".join(f"{name} {score:.1f}/10" for name, score in strongest)
            parts.append(f"Your strongest recent vow alignments were {rendered}.")
        weakest = self.weakest_vows(limit=2)
        if weakest:
            rendered = ", ".join(f"{name} {score:.1f}/10" for name, score in weakest)
            parts.append(f"Your weakest recent alignments were {rendered}.")
        if self.drift_signals:
            rendered = "; ".join(self.drift_signals[:2])
            parts.append(f"Watch for drift around {rendered}.")
        if self.correction:
            parts.append(f"Carry this correction forward: {self.correction}")
        if not parts:
            return ""
        return "Recent calibration guidance: " + " ".join(parts)

    def focus_area(self) -> str:
        weakest = self.weakest_vows(limit=1)
        if weakest:
            return f"strengthening {weakest[0][0].lower()}"
        if self.correction:
            return _clean_inline(self.correction, 80)
        return ""


def parse_calibration_response(raw: str) -> CalibrationSummary | None:
    """Extract vow scores, drift, correction, and commitment from prose."""
    text = raw.strip()
    if not text:
        return None

    lines = text.replace("\r\n", "\n").splitlines()
    vow_scores: dict[str, float] = {}
    for index, line in enumerate(lines):
        match = re.search(
            r"vow\s*([1-5])\s*:\s*([A-Za-z][A-Za-z ]+)",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue
        name = match.group(2).strip().title()
        if name not in _VOW_ORDER:
            continue
        block = "\n".join(lines[index:index + 4])
        score_match = re.search(
            r"(?:assessment|rating|alignment)\s*[:)]\s*\*{0,2}\s*([0-9]+(?:\.\d+)?)\s*/\s*10",
            block,
            re.IGNORECASE,
        )
        if score_match is None:
            score_match = re.search(
                r"\(\s*alignment\s*:\s*([0-9]+(?:\.\d+)?)\s*/\s*10\s*\)",
                block,
                re.IGNORECASE,
            )
        if score_match is not None:
            vow_scores[name] = _clamp(float(score_match.group(1)), 0.0, 10.0)

    drift_text = _extract_section(text, _DRIFT_TITLES)
    correction_text = _extract_section(text, _CORRECTION_TITLES)
    commitment_text = _extract_section(text, _COMMITMENT_TITLES)

    if not correction_text:
        correction_text = _extract_inline_label(
            text,
            ("Specific Correction", "Intention", "Specific Correction or Intention"),
        )
    if not commitment_text:
        commitment_text = _extract_inline_label(
            text,
            ("Affirmation of Commitment", "Affirmation"),
        )

    summary = CalibrationSummary(
        vow_scores=vow_scores,
        drift_signals=_split_highlights(drift_text, limit=4),
        correction=_clean_inline(correction_text, 320),
        commitment=_clean_inline(commitment_text, 320),
        excerpt=_clean_inline(text, 500),
    )
    return summary if summary.has_signal() else None


def apply_calibration_to_internal_state(
    summary: CalibrationSummary,
    current: InternalState,
    current_tick: int,
) -> InternalState:
    """Blend calibration guidance into the prompt-visible internal state."""
    updated = InternalState.from_dict(current.to_dict())
    focus = summary.focus_area()
    if focus:
        updated.update_focus_depth(focus)
        updated.curiosity_focus = focus
    if summary.correction:
        updated.growth_vector = summary.correction
        updated.curiosity_intensity = max(updated.curiosity_intensity, 0.7)

    mean_alignment = summary.mean_alignment()
    if mean_alignment is not None:
        calibrated_confidence = mean_alignment / 10.0
        updated.confidence_level = _clamp(
            (updated.confidence_level + calibrated_confidence) / 2.0,
            0.0,
            1.0,
        )
        weakest = summary.weakest_vows(limit=1)
        if weakest:
            weakest_alignment = weakest[0][1] / 10.0
            updated.epistemic_humility = max(
                updated.epistemic_humility,
                _clamp(1.1 - weakest_alignment, 0.0, 1.0),
            )

    updated.uncertainty_topics = _unique(
        [
            *summary.drift_signals,
            *[f"alignment drift in {name.lower()}" for name, _ in summary.weakest_vows(limit=2)],
            *updated.uncertainty_topics,
        ],
        limit=5,
    )
    updated.engagement_level = max(updated.engagement_level, 0.65)
    updated.attention_mode = "integrating"
    updated.last_updated_tick = current_tick
    return updated


def apply_calibration_to_self_model(
    summary: CalibrationSummary,
    current: SelfModel,
    current_tick: int,
) -> SelfModel:
    """Blend calibration guidance into the explicit self-model."""
    updated = SelfModel.from_dict(current.to_dict())
    updated.growth_edges = _unique(
        [summary.correction, *updated.growth_edges],
        limit=3,
    )
    updated.recent_failures = _unique(
        [*summary.drift_signals, *updated.recent_failures],
        limit=4,
    )
    updated.suspected_blind_spots = _unique(
        [
            *[
                f"blind spot around {name.lower()} alignment"
                for name, _ in summary.weakest_vows(limit=2)
            ],
            *updated.suspected_blind_spots,
        ],
        limit=3,
    )
    updated.known_unknowns = _unique(
        [
            *[
                f"how to strengthen {name.lower()} in practice"
                for name, _ in summary.weakest_vows(limit=2)
            ],
            *updated.known_unknowns,
        ],
        limit=4,
    )
    updated.core_values = _unique(
        [
            *[name for name, _ in summary.strongest_vows(limit=3)],
            *updated.core_values,
        ],
        limit=5,
    )
    focus = summary.focus_area()
    if focus:
        updated.evolving_interests = _unique(
            [focus, *updated.evolving_interests],
            limit=5,
        )
    if summary.commitment:
        updated.working_hypotheses = _unique(
            [summary.commitment, *updated.working_hypotheses],
            limit=5,
        )
    updated.self_model_confidence = _clamp(updated.self_model_confidence + 0.05, 0.0, 1.0)
    updated.last_updated_tick = current_tick
    return updated
