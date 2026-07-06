#!/usr/bin/env python3
"""Daily digest machine.

Every run spends a bounded labor budget gathering candidate papers on two
tracks (frontier: new arXiv submissions; foundation: a rotating retrospective
search over the last few years ranked by citations), ranks them in two stages
(cheap keyword filter, then LLM scoring), and admits at most a handful of
papers above a quality threshold into a growing, committed library
(data/library.json). The library index doubles as the dedupe record, so
repeated runs are idempotent.

Exit codes:
  0 - ran successfully (possibly adding zero papers)
  2 - a fetch/API step failed; nothing was written
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_papers as cp

SEMANTIC_SCHOLAR_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
S2_FIELDS = "paperId,title,abstract,authors,year,publicationDate,url,openAccessPdf,venue,externalIds,citationCount"

DEFAULT_CONFIG = Path("config/interests.json")
DEFAULT_LIBRARY = Path("data/library.json")
DEFAULT_WEB_LIBRARY = Path("web/data/library.json")
DEFAULT_STATE = Path("data/digest_state.json")
DEFAULT_REJECTED = Path("data/rejected_log.jsonl")

SCORE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "justification": {"type": "string"},
    },
    "required": ["score", "justification"],
    "additionalProperties": False,
}


class DigestError(RuntimeError):
    """A fetch/API step failed; the run must exit non-zero."""


def digest_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("digest") if isinstance(config.get("digest"), dict) else {}

    def setting(env_name: str, key: str, default: Any, cast: Any) -> Any:
        env_value = os.getenv(env_name)
        if env_value is not None and env_value.strip():
            try:
                return cast(env_value)
            except (TypeError, ValueError):
                pass
        try:
            return cast(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    settings = {
        "max_labor_paper": max(1, setting("MAX_LABOR_PAPER", "max_labor_paper", 200, int)),
        "max_daily_added": max(0, setting("MAX_DAILY_ADDED", "max_daily_added", 5, int)),
        "min_score": setting("MIN_SCORE", "min_score", 7.0, float),
        "shortlist_size": max(1, setting("SHORTLIST_SIZE", "shortlist_size", 40, int)),
        "frontier_budget_ratio": min(0.95, max(0.05, setting("FRONTIER_BUDGET_RATIO", "frontier_budget_ratio", 0.75, float))),
        # 4 days rather than 1-2: arXiv pauses announcements on weekends, and
        # Monday batches contain papers submitted up to ~3.5 days earlier.
        # The library index dedupes, so an overlapping window costs nothing.
        "frontier_lookback_days": max(1, setting("FRONTIER_LOOKBACK_DAYS", "frontier_lookback_days", 4, int)),
        "foundation_lookback_years": max(1, setting("FOUNDATION_LOOKBACK_YEARS", "foundation_lookback_years", 3, int)),
        "foundation_min_citations": max(0, setting("FOUNDATION_MIN_CITATIONS", "foundation_min_citations", 20, int)),
    }
    settings["frontier_budget"] = max(1, int(settings["max_labor_paper"] * settings["frontier_budget_ratio"]))
    settings["foundation_budget"] = max(1, settings["max_labor_paper"] - settings["frontier_budget"])
    return settings


def normalize_arxiv_id(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    raw = raw.rsplit("/abs/", 1)[-1]
    return re.sub(r"v\d+$", "", raw)


def candidate_keys(paper: dict[str, Any]) -> set[str]:
    keys = set()
    for value in (paper.get("id"), paper.get("arxiv_id")):
        text = str(value or "").strip().lower()
        if text:
            keys.add(text)
    arxiv_id = str(paper.get("arxiv_id") or "").strip().lower()
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    title_key = cp.title_match_key(str(paper.get("title") or ""))
    if title_key:
        keys.add(f"title:{title_key}")
    return keys


def load_library(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            library = cp.load_json(path)
            if isinstance(library, dict) and isinstance(library.get("papers"), list):
                return library
        except Exception as exc:
            raise DigestError(f"cannot read library index {path}: {exc}") from exc
    return {"version": 1, "updated_at_iso": "", "topics": [], "papers": []}


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            state = cp.load_json(path)
            if isinstance(state, dict):
                return state
        except Exception as exc:
            print(f"Warning: cannot read digest state, starting fresh: {exc}", file=sys.stderr)
    return {"foundation_rotation_index": 0, "added_by_day": {}}


def load_rejected_keys(path: Path) -> set[str]:
    """IDs/titles previously scored below threshold; never re-scored."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("reason") != "below_threshold":
                continue
            keys |= candidate_keys(entry)
    return keys


def frontier_candidate(paper: dict[str, Any], topic: cp.Topic) -> dict[str, Any]:
    arxiv_id = normalize_arxiv_id(str(paper.get("id") or ""))
    return {
        "id": f"arxiv:{arxiv_id}" if arxiv_id else str(paper.get("id") or paper.get("paper_url") or ""),
        "arxiv_id": arxiv_id,
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "summary": paper.get("summary", ""),
        "published": paper.get("published", ""),
        "paper_url": paper.get("paper_url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "categories": paper.get("categories", []),
        "venue": "",
        "track": "frontier",
        "seed_topic": topic.id,
        "citation_count": None,
    }


def gather_frontier(
    topics: list[cp.Topic],
    settings: dict[str, Any],
    skip_keys: set[str],
    now: dt.datetime,
) -> list[dict[str, Any]]:
    budget = settings["frontier_budget"]
    per_topic = max(1, budget // max(1, len(topics)))
    cutoff = now - dt.timedelta(days=settings["frontier_lookback_days"])
    delay_seconds = float(os.getenv("DIGEST_ARXIV_DELAY_SECONDS", "3"))
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    skipped_known = 0
    for index, topic in enumerate(topics):
        if index and delay_seconds > 0:
            time.sleep(delay_seconds)
        print(f"[frontier] fetching arXiv for topic: {topic.name}", flush=True)
        try:
            papers = cp.fetch_arxiv_query(
                cp.arxiv_query_for_topic(topic),
                per_topic,
                sort_by="submittedDate",
                sort_order="descending",
                label=f"frontier:{topic.name}",
            )
        except Exception as exc:
            raise DigestError(f"arXiv frontier fetch failed for topic {topic.name}: {exc}") from exc
        for paper in papers:
            published = cp.parse_datetime(str(paper.get("published") or paper.get("updated") or ""))
            if not published or published < cutoff:
                continue
            candidate = frontier_candidate(paper, topic)
            keys = candidate_keys(candidate)
            if keys & skip_keys:
                skipped_known += 1
                continue
            if keys & seen_keys:
                continue
            seen_keys |= keys
            candidates.append(candidate)
    candidates = candidates[:budget]
    print(f"[frontier] {len(candidates)} new candidates (skipped {skipped_known} already known)", flush=True)
    return candidates


def foundation_candidate(item: dict[str, Any], topic: cp.Topic) -> dict[str, Any] | None:
    base = cp.semantic_scholar_paper_from_item(item)
    if not base:
        return None
    external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    arxiv_id = normalize_arxiv_id(str(external_ids.get("ArXiv") or ""))
    citation_count = item.get("citationCount")
    candidate = {
        "id": f"arxiv:{arxiv_id}" if arxiv_id else base["id"],
        "arxiv_id": arxiv_id,
        "title": base["title"],
        "authors": base["authors"],
        "summary": base["summary"],
        "published": base["published"],
        "paper_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else base["paper_url"],
        "pdf_url": base["pdf_url"] or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""),
        "categories": base["categories"],
        "venue": str(item.get("venue") or ""),
        "track": "foundation",
        "seed_topic": topic.id,
        "citation_count": int(citation_count) if isinstance(citation_count, (int, float)) else None,
    }
    return candidate


def foundation_query(topic: cp.Topic) -> str:
    terms = []
    for keyword in topic.keywords[:8]:
        keyword = keyword.strip()
        if not keyword:
            continue
        terms.append(f'"{keyword}"' if " " in keyword else keyword)
    return " | ".join(terms) or topic.name


def semantic_scholar_headers() -> dict[str, str]:
    headers = {"User-Agent": cp.collector_user_agent()}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def gather_foundation(
    topic: cp.Topic,
    settings: dict[str, Any],
    skip_keys: set[str],
    now: dt.datetime,
) -> list[dict[str, Any]]:
    budget = settings["foundation_budget"]
    year_min = now.year - settings["foundation_lookback_years"]
    timeout = float(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", "60"))
    params = {
        "query": foundation_query(topic),
        "year": f"{year_min}-",
        "sort": "citationCount:desc",
        "minCitationCount": str(settings["foundation_min_citations"]),
        "fields": S2_FIELDS,
    }
    print(f"[foundation] retrospective search for topic: {topic.name} (since {year_min})", flush=True)
    try:
        data = cp.request_json(
            f"{SEMANTIC_SCHOLAR_BULK_URL}?{urllib.parse.urlencode(params)}",
            headers=semantic_scholar_headers(),
            timeout=timeout,
        )
        items = data.get("data") or []
    except Exception as bulk_exc:
        print(f"Warning: Semantic Scholar bulk search failed ({bulk_exc}); falling back to relevance search", file=sys.stderr)
        fallback_params = {
            "query": cp.topic_plain_query(topic),
            "year": f"{year_min}-",
            "limit": "100",
            "fields": S2_FIELDS,
        }
        try:
            data = cp.request_json(
                f"{cp.SEMANTIC_SCHOLAR_SEARCH_URL}?{urllib.parse.urlencode(fallback_params)}",
                headers=semantic_scholar_headers(),
                timeout=timeout,
            )
            items = data.get("data") or []
        except Exception as exc:
            raise DigestError(f"Semantic Scholar foundation search failed for topic {topic.name}: {exc}") from exc

    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    skipped_known = 0
    skipped_no_abstract = 0
    min_citations = settings["foundation_min_citations"]
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = foundation_candidate(item, topic)
        if not candidate:
            continue
        if (candidate["citation_count"] or 0) < min_citations:
            continue
        if not cp.has_meaningful_summary(candidate):
            skipped_no_abstract += 1
            continue
        keys = candidate_keys(candidate)
        if keys & skip_keys:
            skipped_known += 1
            continue
        if keys & seen_keys:
            continue
        seen_keys |= keys
        candidates.append(candidate)
        if len(candidates) >= budget:
            break
    print(
        f"[foundation] {len(candidates)} new candidates "
        f"(skipped {skipped_known} already known, {skipped_no_abstract} without abstract)",
        flush=True,
    )
    return candidates


def citation_weight(citation_count: int | None) -> float:
    if not citation_count or citation_count <= 0:
        return 0.0
    # log10 scale: 10 citations -> ~0.35, 100 -> ~0.67, 1000+ -> 1.0
    return min(1.0, math.log10(citation_count + 1) / 3.0)


def stage1_shortlist(
    frontier: list[dict[str, Any]],
    foundation: list[dict[str, Any]],
    topics: list[cp.Topic],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    for candidate in [*frontier, *foundation]:
        matches = sorted(
            (cp.score_paper(topic, candidate) for topic in topics),
            key=lambda match: match["score"],
            reverse=True,
        )
        candidate["best_match"] = matches[0]
        relevance = matches[0]["score"]
        if candidate["track"] == "foundation":
            candidate["stage1_score"] = round(0.6 * relevance + 0.4 * citation_weight(candidate.get("citation_count")), 4)
        else:
            candidate["stage1_score"] = round(relevance, 4)

    frontier_sorted = sorted(frontier, key=lambda p: p["stage1_score"], reverse=True)
    foundation_sorted = sorted(foundation, key=lambda p: p["stage1_score"], reverse=True)

    size = settings["shortlist_size"]
    frontier_slots = min(len(frontier_sorted), round(size * settings["frontier_budget_ratio"]))
    foundation_slots = min(len(foundation_sorted), size - frontier_slots)
    shortlist = frontier_sorted[:frontier_slots] + foundation_sorted[:foundation_slots]
    # Refill unused slots from whichever track has leftovers.
    leftovers = frontier_sorted[frontier_slots:] + foundation_sorted[foundation_slots:]
    leftovers.sort(key=lambda p: p["stage1_score"], reverse=True)
    shortlist.extend(leftovers[: max(0, size - len(shortlist))])
    return shortlist


def research_directions_block(topics: list[cp.Topic]) -> str:
    lines = []
    for topic in topics:
        lines.append(f"- {topic.name}: {topic.description}")
    return "\n".join(lines)


def build_score_prompt(paper: dict[str, Any], topics: list[cp.Topic]) -> str:
    track_label = (
        "前沿新论文（最近一两天的 arXiv 新投稿）"
        if paper["track"] == "frontier"
        else "近几年经典/基础论文（回溯检索，按引用数初筛）"
    )
    citation_line = ""
    if paper.get("citation_count") is not None:
        citation_line = f"引用数：{paper['citation_count']}\n"
    venue_line = f"发表场所：{paper['venue']}\n" if paper.get("venue") else ""
    best = paper.get("best_match") or {}
    return f"""
你是一名严格的学术论文评审，为一个「每日精选文库」打分。宁缺毋滥：与研究方向无关或质量平庸的论文必须给低分。只输出合法 JSON。

我的研究方向：
{research_directions_block(topics)}

候选论文（赛道：{track_label}）：
标题：{paper.get("title", "")}
作者：{", ".join(paper.get("authors", [])[:8])}
发表时间：{str(paper.get("published", ""))[:10]}
{venue_line}{citation_line}分类：{", ".join(paper.get("categories", [])[:8])}
初筛最相关方向：{best.get("topic_name", "")}
摘要：{paper.get("summary", "")}

评分标准（0-10）：
- 前沿赛道：评估与我研究方向的相关性、工作质量与新颖性。
- 经典赛道：评估与我研究方向的相关性、基础性与影响力（引用数、方法是否成为常用基线）。
- 9-10：方向高度相关且是必读级别的工作；7-8：方向相关、质量扎实；5-6：仅边缘相关或质量一般；0-4：不相关或明显平庸。

输出 JSON：
{{"score": 0 到 10 的数字, "justification": "一句话中文理由，说明论文的具体贡献以及匹配（或不匹配）哪个研究方向"}}
""".strip()


def score_one(paper: dict[str, Any], topics: list[cp.Topic]) -> dict[str, Any]:
    prompt = build_score_prompt(paper, topics)
    last_error: Exception | None = None
    for _ in range(2):
        try:
            data = cp.call_llm(prompt, schema=SCORE_JSON_SCHEMA)
            score = float(data.get("score"))
            justification = cp.normalize_space(str(data.get("justification") or ""))
            if not justification:
                raise ValueError("empty justification")
            return {"score": max(0.0, min(10.0, score)), "justification": justification}
        except Exception as exc:
            last_error = exc
    raise DigestError(f"LLM scoring failed for {paper.get('id')}: {last_error}")


def stage2_score(shortlist: list[dict[str, Any]], topics: list[cp.Topic]) -> None:
    if not shortlist:
        return
    if not cp.llm_enabled():
        raise DigestError(
            "LLM scoring is required but no backend is configured "
            "(set LLM_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY)."
        )
    concurrency = max(1, cp.env_int("LLM_CONCURRENCY", 2))
    print(f"[stage2] LLM-scoring {len(shortlist)} shortlisted papers (concurrency={concurrency})", flush=True)
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(score_one, paper, topics): paper for paper in shortlist}
        for future in concurrent.futures.as_completed(futures):
            paper = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failures.append(str(exc))
                continue
            paper["llm_score"] = result["score"]
            paper["justification"] = result["justification"]
            print(
                f"[stage2] {paper['llm_score']:.1f} [{paper['track']}] {str(paper.get('title'))[:80]}",
                flush=True,
            )
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise DigestError(f"{len(failures)} of {len(shortlist)} LLM scoring calls failed")


def admit(
    shortlist: list[dict[str, Any]],
    settings: dict[str, Any],
    remaining_cap: int,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    scored = sorted(shortlist, key=lambda p: p.get("llm_score", 0.0), reverse=True)
    eligible = [p for p in scored if p.get("llm_score", 0.0) >= settings["min_score"]]
    admitted = eligible[:remaining_cap]
    rejected: list[tuple[dict[str, Any], str]] = []
    rejected.extend((paper, "over_cap") for paper in eligible[remaining_cap:])
    rejected.extend((paper, "below_threshold") for paper in scored if paper.get("llm_score", 0.0) < settings["min_score"])
    return admitted, rejected


def summarize_admitted(admitted: list[dict[str, Any]], topics: list[cp.Topic]) -> None:
    topics_by_id = {topic.id: topic for topic in topics}
    for paper in admitted:
        best = paper.get("best_match") or {}
        topic = topics_by_id.get(str(best.get("topic_id")))
        if not topic:
            paper["chinese_summary"] = None
            continue
        summary, _ = cp.summarize_with_llm(topic, paper, best)
        paper["chinese_summary"] = summary


def library_entry(paper: dict[str, Any], today: str) -> dict[str, Any]:
    best = paper.get("best_match") or {}
    return {
        "id": paper["id"],
        "arxiv_id": paper.get("arxiv_id", ""),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "published": paper.get("published", ""),
        "summary": paper.get("summary", ""),
        "paper_url": paper.get("paper_url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "categories": paper.get("categories", []),
        "venue": paper.get("venue", ""),
        "track": paper["track"],
        "topic_id": str(best.get("topic_id") or ""),
        "topic_name": str(best.get("topic_name") or ""),
        "score": round(float(paper.get("llm_score", 0.0)), 1),
        "justification": paper.get("justification", ""),
        "citation_count": paper.get("citation_count"),
        "date_added": today,
        "chinese_summary": paper.get("chinese_summary"),
    }


def rejected_entry(paper: dict[str, Any], reason: str, today: str) -> dict[str, Any]:
    best = paper.get("best_match") or {}
    return {
        "date": today,
        "id": paper.get("id", ""),
        "arxiv_id": paper.get("arxiv_id", ""),
        "title": paper.get("title", ""),
        "track": paper.get("track", ""),
        "topic_id": str(best.get("topic_id") or ""),
        "score": round(float(paper.get("llm_score", 0.0)), 1),
        "justification": paper.get("justification", ""),
        "citation_count": paper.get("citation_count"),
        "reason": reason,
    }


def append_rejected(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def prune_added_by_day(added_by_day: dict[str, Any], today: dt.date, keep_days: int = 7) -> dict[str, int]:
    pruned = {}
    for day, count in added_by_day.items():
        try:
            parsed = dt.date.fromisoformat(str(day))
        except ValueError:
            continue
        if (today - parsed).days <= keep_days:
            pruned[str(day)] = int(count)
    return pruned


def run_digest(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    config = cp.load_json(args.config)
    topics = cp.parse_topics(config)
    settings = digest_settings(config)
    print(f"Digest settings: {json.dumps(settings, ensure_ascii=False)}", flush=True)

    library = load_library(args.library)
    state = load_state(args.state)
    state["added_by_day"] = prune_added_by_day(state.get("added_by_day") or {}, now.date())

    added_today = int(state["added_by_day"].get(today, 0))
    remaining_cap = max(0, settings["max_daily_added"] - added_today)
    if remaining_cap == 0:
        print(
            f"DIGEST OK: daily cap already reached ({added_today}/{settings['max_daily_added']} added today); "
            "nothing to do.",
            flush=True,
        )
        state["last_run_iso"] = now.isoformat()
        state["last_run_status"] = "ok-cap-reached"
        if not args.dry_run:
            cp.write_json(args.state, state)
        return 0

    skip_keys: set[str] = set()
    for paper in library["papers"]:
        skip_keys |= candidate_keys(paper)
    skip_keys |= load_rejected_keys(args.rejected)

    # Candidate gathering (bounded by max_labor_paper).
    frontier = gather_frontier(topics, settings, skip_keys, now)
    frontier_keys: set[str] = set()
    for candidate in frontier:
        frontier_keys |= candidate_keys(candidate)
    rotation_index = int(state.get("foundation_rotation_index", 0)) % len(topics)
    foundation_topic = topics[rotation_index]
    foundation = gather_foundation(foundation_topic, settings, skip_keys | frontier_keys, now)

    # Two-stage ranking.
    shortlist = stage1_shortlist(frontier, foundation, topics, settings)
    print(
        f"[stage1] shortlisted {len(shortlist)} of {len(frontier) + len(foundation)} candidates "
        f"({sum(1 for p in shortlist if p['track'] == 'frontier')} frontier, "
        f"{sum(1 for p in shortlist if p['track'] == 'foundation')} foundation)",
        flush=True,
    )
    stage2_score(shortlist, topics)

    # Admission.
    admitted, rejected = admit(shortlist, settings, remaining_cap)
    summarize_admitted(admitted, topics)

    entries = [library_entry(paper, today) for paper in admitted]
    library["papers"].extend(entries)
    library["updated_at_iso"] = now.isoformat()
    library["topics"] = [{"id": topic.id, "name": topic.name} for topic in topics]
    library["version"] = 1

    state["last_run_iso"] = now.isoformat()
    state["last_run_status"] = "ok"
    state["foundation_rotation_index"] = rotation_index + 1
    state["foundation_last_topic"] = foundation_topic.id
    state["added_by_day"][today] = added_today + len(entries)

    if args.dry_run:
        print("[dry-run] skipping writes", flush=True)
    else:
        cp.write_json(args.library, library)
        if args.web_library:
            cp.write_json(args.web_library, library)
        append_rejected(args.rejected, [rejected_entry(paper, reason, today) for paper, reason in rejected])
        cp.write_json(args.state, state)

    for entry in entries:
        print(f"ADMITTED {entry['score']:.1f} [{entry['track']}] {entry['title']} — {entry['justification']}", flush=True)
    if not entries:
        print(
            f"DIGEST OK: ran successfully, but no paper passed the quality bar today "
            f"(threshold={settings['min_score']}, shortlist={len(shortlist)}).",
            flush=True,
        )
    print(
        f"DIGEST OK: added={len(entries)} (cap {remaining_cap}), rejected={len(rejected)}, "
        f"library_total={len(library['papers'])}, foundation_topic={foundation_topic.id}",
        flush=True,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily digest machine.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--web-library", type=Path, default=DEFAULT_WEB_LIBRARY)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        sys.exit(run_digest(args))
    except DigestError as exc:
        print(f"DIGEST FAILED: {exc}", file=sys.stderr, flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
