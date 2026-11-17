from dotenv import load_dotenv
load_dotenv()

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

TIME_RE = re.compile(r"Start:\s*\((\d{2}):(\d{2}(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d{2}):(\d{2}(?:\.\d+)?)\)")
EP_RE = re.compile(r"EPISODE NUMBER:\s*(.+)", re.I)

@dataclass
class Event:
    start: float
    end: float
    category: str
    speaker: str
    text: str


def to_seconds(mm: str, ss: str) -> float:
    return int(mm) * 60 + float(ss)


def to_timestamp(value: float) -> str:
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


def parse_episode(text: str, filename: str = "episode.txt"):
    ep_match = EP_RE.search(text)
    episode = ep_match.group(1).strip() if ep_match else Path(filename).stem
    logline_match = re.search(r"EPISODE LOGLINE:\s*(.+)", text)
    logline = logline_match.group(1).strip() if logline_match else ""
    lines = text.splitlines()
    events = []
    for idx, line in enumerate(lines):
        match = TIME_RE.search(line)
        if not match:
            continue
        start = to_seconds(match.group(1), match.group(2))
        end = to_seconds(match.group(3), match.group(4))
        if end <= start:
            continue
        heading = ""
        for back in range(idx - 1, max(-1, idx - 5), -1):
            candidate = lines[back].strip()
            if candidate.startswith("**") and candidate.endswith("**"):
                heading = candidate.strip("*").strip()
                break
            if candidate.startswith("SCENE "):
                break
        content = ""
        for forward in range(idx + 1, min(len(lines), idx + 4)):
            candidate = lines[forward].strip()
            if candidate and not candidate.startswith("**") and not candidate.startswith("Start:"):
                content = candidate.strip()
                break
        upper = heading.upper()
        category = "camera" if "CAMERA" in upper else "action" if "ACTION" in upper else "sound" if "SOUND" in upper else "dialogue"
        speaker = heading if category == "dialogue" else ""
        events.append(Event(start, end, category, speaker, content))
    events.sort(key=lambda e: (e.start, e.end))
    return episode, logline, events


def _event_dict(e):
    return {"start": to_timestamp(e.start), "end": to_timestamp(e.end),
            "start_seconds": e.start, "end_seconds": e.end,
            "type": e.category, "speaker": e.speaker, "content": e.text}


# Story angles are editorial categories, not claims that every episode contains every beat.
STORY_BEAT_RULES = {
    "betrayal_reveal": ["marrying me", "marrying her", "not anymore", "because he is marrying", "changed my mind", "waiting for me"],
    "secret_confession": ["of course i knew", "never going to marry", "long enough to know", "i knew"],
    "family_abandonment": ["father", "please look at me", "tell them this is a mistake", "refuses to look", "looks at the floor"],
    "public_humiliation": ["embarrassed this family", "daughter people tolerate", "daughter they choose", "jewel of this family"],
    "defiant_turn": ["hand clenches", "clenches the gown", "face hardens", "cold, resolute", "defiance", "hardens from devastation"]
}


def detect_story_beats(events: List[Event]):
    """Find distinct narrative anchors using only screenplay event text and metadata."""
    beats = []
    for angle, terms in STORY_BEAT_RULES.items():
        hits = []
        for index, event in enumerate(events):
            haystack = (event.text + " " + event.speaker).lower()
            if any(term in haystack for term in terms):
                hits.append(index)
        if hits:
            beats.append({"angle": angle, "anchor_indexes": hits})
    # Fallback: divide an episode into temporal thirds only when no semantic anchors were found.
    if not beats and events:
        lo, hi = events[0].start, events[-1].end
        for n, angle in enumerate(("opening_turn", "midpoint_turn", "closing_turn")):
            target = lo + (hi - lo) * (n + 0.5) / 3
            nearest = min(range(len(events)), key=lambda i: abs((events[i].start + events[i].end) / 2 - target))
            beats.append({"angle": angle, "anchor_indexes": [nearest]})
    return beats


def _boundary_info(events, start, end):
    # Check every event type. Dialogue/action/sound cuts are hard warnings;
    # camera cuts are still reported because they can make the edit visually abrupt.
    start_cuts = [e for e in events if e.start < start < e.end]
    end_cuts = [e for e in events if e.start < end < e.end]
    core_types = {"dialogue", "action", "sound"}
    return {
        "start_cuts_event": bool(start_cuts), "end_cuts_event": bool(end_cuts),
        "start_cuts_core_event": any(e.category in core_types for e in start_cuts),
        "end_cuts_core_event": any(e.category in core_types for e in end_cuts),
        "cut_events": [_event_dict(e) for e in start_cuts + end_cuts]
    }


def _make_candidate(events, start, end, angle, anchor_indexes):
    included = [e for e in events if e.end > start and e.start < end]
    anchor_events = [events[i] for i in anchor_indexes if events[i].start >= start and events[i].end <= end]
    warnings = _boundary_info(events, start, end)
    return {
        "start_seconds": start, "end_seconds": end,
        "start": to_timestamp(start), "end": to_timestamp(end),
        "duration_seconds": round(end - start, 2),
        "story_angle": angle,
        "anchor_evidence": [_event_dict(e) for e in anchor_events],
        "boundary_warnings": warnings,
        "timeline": [_event_dict(e) for e in included]
    }


def _cluster_anchor_indexes(events, indexes, max_gap=8.0):
    """Group related anchor events into one narrative beat by timestamp proximity."""
    ordered = sorted(set(indexes), key=lambda i: (events[i].start, events[i].end))
    clusters = []
    for index in ordered:
        event = events[index]
        if not clusters or event.start - max(events[i].end for i in clusters[-1]) > max_gap:
            clusters.append([index])
        else:
            clusters[-1].append(index)
    return clusters


def make_story_candidates(events: List[Event], min_duration=20, max_duration=60, per_beat=2):
    """Build beat-centred windows; avoid reusing one broad window for several story angles."""
    if not events:
        return [], []
    beats = detect_story_beats(events)
    points = sorted({round(v, 2) for e in events for v in (e.start, e.end)})
    result = []
    for beat in beats:
        beat_options = []
        clusters = _cluster_anchor_indexes(events, beat["anchor_indexes"], max_gap=8.0)
        for cluster in clusters:
            cluster_start = min(events[i].start for i in cluster)
            cluster_end = max(events[i].end for i in cluster)
            # Candidate boundaries must contain the complete anchor cluster.
            starts = [p for p in points if p <= cluster_start]
            ends = [p for p in points if p >= cluster_end]
            for start in starts:
                before = cluster_start - start
                if before > 16.0:
                    continue
                for end in ends:
                    after = end - cluster_end
                    duration = end - start
                    if after > 10.0 or duration < min_duration or duration > max_duration:
                        continue
                    candidate = _make_candidate(events, start, end, beat["angle"], cluster)
                    warnings = candidate["boundary_warnings"]
                    # Dialogue, action and sound must never be cut. Camera overlaps
                    # are retained as warnings so an editor can inspect the shot.
                    if warnings["start_cuts_core_event"] or warnings["end_cuts_core_event"]:
                        continue
                    before = cluster_start - start
                    after = end - cluster_end
                    # Reward concise setup/payoff around this beat, not unrelated events.
                    context_penalty = abs(before - 6.0) * 0.8 + abs(after - 5.0) * 0.8
                    duration_penalty = abs(duration - 30.0) * 0.30
                    camera_penalty = 5 * sum(warnings[k] for k in ("start_cuts_event", "end_cuts_event"))
                    candidate["local_score"] = heuristic_score(candidate) - context_penalty - duration_penalty - camera_penalty
                    candidate["anchor_cluster"] = [_event_dict(events[i]) for i in cluster]
                    beat_options.append(candidate)
        beat_options.sort(key=lambda c: c["local_score"], reverse=True)
        kept = []
        for candidate in beat_options:
            if all(_overlap_ratio(candidate, old) < 0.55 for old in kept):
                kept.append(candidate)
            if len(kept) >= per_beat:
                break
        result.extend(kept)
    for idx, candidate in enumerate(result):
        candidate["candidate_id"] = idx
        candidate["score"] = candidate["local_score"]
    return result, beats


def heuristic_score(candidate):
    text = " ".join(x["content"] for x in candidate["timeline"]).lower()
    hook_terms = ["no", "what", "why", "betray", "marry", "secret", "shocked", "never", "stop", "look"]
    cliff_terms = ["please", "never", "secret", "truth", "who", "what", "enough", "but", "because"]
    emotion_terms = ["cry", "tear", "heartbroken", "furious", "betrayed", "shocked", "desperate", "cold", "gloating"]
    hook = min(10, sum(term in text for term in hook_terms))
    cliff = min(10, sum(term in text for term in cliff_terms))
    emotion = min(10, sum(term in text for term in emotion_terms))
    dialogue_count = sum(x["type"] == "dialogue" for x in candidate["timeline"])
    clarity = min(10, dialogue_count)
    boundary_penalty = 8 * sum(candidate.get("boundary_warnings", {}).get(k, False) for k in ("start_cuts_core_event", "end_cuts_core_event"))
    return max(0, round((hook * 3 + cliff * 3 + emotion * 2 + clarity * 2) * 2.5 - boundary_penalty))


def _overlap_ratio(a, b):
    a0, a1 = float(a["start_seconds"]), float(a["end_seconds"])
    b0, b1 = float(b["start_seconds"]), float(b["end_seconds"])
    intersection = max(0.0, min(a1, b1) - max(a0, b0))
    shorter = max(0.001, min(a1 - a0, b1 - b0))
    return intersection / shorter


def select_diverse_candidates(candidates, limit=5, overlap_threshold=0.55):
    """Prefer one result per story angle before allowing a second from the same angle."""
    ranked = sorted(candidates, key=lambda c: float(c.get("score", 0)), reverse=True)
    selected, used_angles = [], set()
    for candidate in ranked:
        angle = candidate.get("story_angle", "unclassified")
        if angle in used_angles:
            continue
        if all((candidate["start_seconds"], candidate["end_seconds"]) != (kept["start_seconds"], kept["end_seconds"]) and _overlap_ratio(candidate, kept) < overlap_threshold for kept in selected):
            selected.append(candidate)
            used_angles.add(angle)
        if len(selected) >= limit:
            return selected
    # If fewer than limit distinct angles exist, fill only with genuinely non-duplicate alternatives.
    for candidate in ranked:
        if candidate in selected:
            continue
        if all((candidate["start_seconds"], candidate["end_seconds"]) != (kept["start_seconds"], kept["end_seconds"]) and _overlap_ratio(candidate, kept) < overlap_threshold for kept in selected):
            selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _normalize_quote(value):
    return re.sub(r"\s+", " ", str(value)).strip().strip('"“”\'')


def validate_evidence(evidence, timeline, clip_start=None, clip_end=None):
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        return []
    valid = []
    for item in evidence:
        if isinstance(item, str):
            quote, claimed_type, claimed_time = item, None, None
        elif isinstance(item, dict):
            quote = item.get("quote", item.get("text", ""))
            claimed_type, claimed_time = item.get("type"), item.get("time")
        else:
            continue
        normalized = _normalize_quote(quote)
        if not normalized:
            continue
        for event in timeline:
            source = _normalize_quote(event.get("content", ""))
            fully_inside = (clip_start is None or event.get("start_seconds", 0) >= clip_start) and (clip_end is None or event.get("end_seconds", 0) <= clip_end)
            if fully_inside and normalized in source and (not claimed_type or claimed_type == event["type"]) and (not claimed_time or claimed_time == event["start"]):
                valid.append({"type": event["type"], "time": event["start"], "end": event["end"], "quote": quote})
                break
    unique, seen = [], set()
    for item in valid:
        key = (item["type"], item["time"], item["quote"])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def _compact_candidate(candidate, max_events=7, max_chars=150):
    timeline = candidate["timeline"]
    # Always include the event that defines this story angle, then sample the rest
    # across the clip so the model sees the opening, development and ending.
    anchor_keys = {(e["start"], e["end"], e["content"]) for e in candidate.get("anchor_evidence", [])}
    anchors = [e for e in timeline if (e["start"], e["end"], e["content"]) in anchor_keys]
    remaining = [e for e in timeline if e not in anchors]
    slots = max(0, max_events - len(anchors))
    if len(remaining) > slots and slots > 0:
        indexes = sorted({round(i * (len(remaining) - 1) / max(1, slots - 1)) for i in range(slots)})
        sampled = [remaining[i] for i in indexes]
    else:
        sampled = remaining[:slots] if slots else []
    chosen = sorted(anchors + sampled, key=lambda e: (e["start_seconds"], e["end_seconds"]))
    compact_events = [{
        "start": e["start"], "end": e["end"], "type": e["type"],
        "speaker": e.get("speaker", "")[:70], "content": str(e.get("content", ""))[:max_chars]
    } for e in chosen]
    return {
        "candidate_id": candidate["candidate_id"], "story_angle": candidate["story_angle"],
        "start": candidate["start"], "end": candidate["end"],
        "duration_seconds": candidate["duration_seconds"],
        "boundary_warnings": candidate["boundary_warnings"], "events": compact_events
    }


def estimate_prompt_tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:
        return max(1, int(len(text) / 3.0))


def _json_schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"candidates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "candidate_id": {"type": "integer"}, "score": {"type": "integer"},
                "story_angle": {"type": "string"}, "hook": {"type": "string"},
                "cliffhanger": {"type": "string"}, "reason": {"type": "string"},
                "standalone_clarity": {"type": "integer"},
                "evidence": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"type": {"type": "string", "enum": ["dialogue", "action", "camera", "sound"]},
                                   "time": {"type": "string"}, "quote": {"type": "string"}},
                    "required": ["type", "time", "quote"]
                }}
            },
            "required": ["candidate_id", "score", "story_angle", "hook", "cliffhanger", "reason", "standalone_clarity", "evidence"]
        }}}, "required": ["candidates"]
    }


def _build_prompt(episode, logline, candidates):
    compact = [_compact_candidate(c) for c in candidates]
    instructions = (
        "You are a microdrama promotional editor. Evaluate each supplied 20-60 second clip as a standalone promo. "
        "The story_angle is a proposed editorial angle; preserve it unless the supplied events clearly contradict it. "
        "Prioritize distinct angles across the episode: betrayal reveal, secret confession, family abandonment, public humiliation, defiant turn. "
        "Score 0-100 for hook, emotional impact, clarity, mini-story coherence and ending strength. "
        "Check every supplied event at the clip boundaries. Penalize a cut that truncates dialogue, action, sound or an important camera event. "
        "Do not claim a cut-to-black or completed reaction unless its full timed event is inside the clip. "
        "Do not invent visuals, events, dialogue or timestamps. Evidence must be verbatim supplied event content with its exact start time. "
        "Give 2-3 evidence items from opening, middle and ending where possible. Keep judgments concise and consistent with the timeline."
    )
    payload = {"episode": episode, "logline": logline[:220], "candidates": compact}
    return instructions + "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _call_groq(prompt: str, model: str, max_output_tokens: int):
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing. Add it to your .env file.")
    client = Groq(api_key=api_key, timeout=90.0, max_retries=0)
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0.2,
        max_completion_tokens=max_output_tokens, reasoning_effort="low",
        response_format={"type": "json_schema", "json_schema": {
            "name": "promo_candidate_evaluations", "strict": True, "schema": _json_schema()
        }}
    )
    content = response.choices[0].message.content or ""
    data = json.loads(content)
    usage = getattr(response, "usage", None)
    actual = {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "cached_input_tokens": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    }
    return data.get("candidates", []), actual


def _call_gemini(prompt: str, model: str, max_output_tokens: int):
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt,
        config={"max_output_tokens": max_output_tokens, "response_mime_type": "application/json"})
    parsed = json.loads((response.text or "").strip())
    evaluations = parsed if isinstance(parsed, list) else parsed.get("candidates", [])
    usage = getattr(response, "usage_metadata", None)
    actual = {
        "input_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None), "cached_input_tokens": None
    }
    return evaluations, actual


def evaluate_candidates(episode, logline, candidates):
    provider = os.getenv("AI_PROVIDER", "groq").strip().lower()
    if provider not in ("groq", "gemini"):
        raise RuntimeError(f"Unsupported AI_PROVIDER '{provider}'. Use groq or gemini.")
    default_model = "openai/gpt-oss-120b" if provider == "groq" else "gemini-3.5-flash-lite"
    model = os.getenv("AI_MODEL", default_model).strip()
    max_output_tokens = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "1800"))
    prompt = _build_prompt(episode, logline, candidates)
    estimate = estimate_prompt_tokens(prompt)
    max_input = int(os.getenv("AI_MAX_ESTIMATED_INPUT_TOKENS", "6500"))
    if estimate > max_input:
        raise RuntimeError(f"Prompt preflight estimate is {estimate} tokens, above configured {max_input}-token safety budget.")
    evaluations, usage = _call_groq(prompt, model, max_output_tokens) if provider == "groq" else _call_gemini(prompt, model, max_output_tokens)
    telemetry = {"provider": provider, "model": model, "estimated_input_tokens": estimate,
        "actual_input_tokens": usage.get("input_tokens"), "actual_output_tokens": usage.get("output_tokens"),
        "actual_total_tokens": usage.get("total_tokens"), "cached_input_tokens": usage.get("cached_input_tokens")}
    return evaluations, telemetry


def _validate_editorial_claims(evaluation, candidate):
    """Flag ending claims unless the complete supporting event is inside the clip."""
    start, end = candidate["start_seconds"], candidate["end_seconds"]
    complete_events = [e for e in candidate["timeline"]
        if e["start_seconds"] >= start and e["end_seconds"] <= end]
    cliff = evaluation.get("cliffhanger", "").lower()
    reason = evaluation.get("reason", "").lower()
    claims = []
    if "cut to black" in cliff or "cut to black" in reason:
        claims.append(any(e["content"].lower().strip().strip(" .") == "cut to black" for e in complete_events))
    if any(term in cliff for term in ("defiance", "defiant", "hardens", "resolute")):
        claims.append(any(any(term in e["content"].lower() for term in ("defiance", "hardens", "resolute", "refusing to break down")) for e in complete_events))
    return all(claims) if claims else True


def analyze_files(uploaded_files, use_ai=True):
    all_results, errors, usage_rows = [], [], []
    for uploaded in uploaded_files:
        try:
            raw = uploaded.getvalue().decode("utf-8", errors="replace")
            episode, logline, events = parse_episode(raw, uploaded.name)
            if not events:
                errors.append(f"{uploaded.name}: no timestamped events detected.")
                continue
            candidates, beats = make_story_candidates(events)
            if not candidates:
                errors.append(f"{uploaded.name}: no boundary-safe 20-60 second candidates found for detected story beats.")
                continue
            if use_ai:
                evaluations, telemetry = evaluate_candidates(episode, logline, candidates)
                usage_rows.append({"episode": episode, **telemetry})
                by_id = {x["candidate_id"]: x for x in evaluations if isinstance(x, dict) and isinstance(x.get("candidate_id"), int)}
                evaluated = []
                for c in candidates:
                    ev = by_id.get(c["candidate_id"])
                    if not ev:
                        continue
                    valid_evidence = validate_evidence(ev.get("evidence", []), c["timeline"], c["start_seconds"], c["end_seconds"])
                    # The model cannot silently relabel the locally detected story angle.
                    c["score"] = int(max(0, min(100, ev.get("score", 0))))
                    c.update({"hook": str(ev.get("hook", "Not supplied by model")),
                        "cliffhanger": str(ev.get("cliffhanger", "Not supplied by model")),
                        "reason": str(ev.get("reason", "Not supplied by model")),
                        "standalone_clarity": ev.get("standalone_clarity"), "evidence": valid_evidence,
                        "evidence_validation": "validated" if valid_evidence else "no verbatim evidence matched",
                        "score_basis": f"{telemetry['provider'].title()} · {telemetry['model']}"})
                    if not _validate_editorial_claims(ev, c):
                        c["editorial_warning"] = "The model's ending description includes a visual/story event not fully supported inside this clip. Review or revise the description."
                    evaluated.append(c)
                if not evaluated:
                    raise RuntimeError("AI returned no usable candidate evaluations. Check model output and request logs.")
                candidates = evaluated
            else:
                for c in candidates:
                    c.update({"hook": "Requires human review", "cliffhanger": "Requires human review",
                        "reason": "Local story-beat candidate; not AI evaluated.", "evidence": [],
                        "evidence_validation": "not evaluated", "score_basis": "Local story-beat heuristic"})
            selected = select_diverse_candidates(candidates, limit=5, overlap_threshold=0.92)
            if len(selected) < 5:
                errors.append(f"{uploaded.name}: found {len(selected)} distinct story angles with boundary-safe windows; returned fewer than five rather than inventing a fifth angle.")
            for c in selected:
                overlaps = []
                for other in selected:
                    if other is c:
                        continue
                    ratio = _overlap_ratio(c, other)
                    if ratio > 0:
                        overlaps.append({"story_angle": other.get("story_angle", "unclassified"),
                                         "overlap_percent_of_shorter_clip": round(ratio * 100, 1)})
                all_results.append({"episode": episode, "start": c["start"], "end": c["end"],
                    "start_seconds": c["start_seconds"], "end_seconds": c["end_seconds"],
                    "duration_seconds": c["duration_seconds"], "score": c["score"],
                    "story_angle": c["story_angle"], "hook": c.get("hook", ""),
                    "cliffhanger": c.get("cliffhanger", ""), "reason": c.get("reason", ""),
                    "evidence": c.get("evidence", []), "evidence_validation": c.get("evidence_validation", "not evaluated"),
                    "boundary_warnings": c.get("boundary_warnings", {}),
                    "editorial_warning": c.get("editorial_warning", ""),
                    "overlaps_with_selected_clips": overlaps,
                    "score_basis": c.get("score_basis", "Unknown"),
                    "segments": [{"start": c["start"], "end": c["end"]}]})
        except Exception as exc:
            errors.append(f"{uploaded.name}: analysis did not complete. {exc}")
    all_results.sort(key=lambda x: (x["episode"], -x["score"]))
    return all_results, errors, usage_rows
