#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import difflib
import email.utils
import html
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ARXIV_API_URL = "https://export.arxiv.org/api/query"
DBLP_API_URL = os.getenv("DBLP_API_URL", "http://dblp.org/search/publ/api")
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
DEFAULT_CONFIG = Path("config/interests.json")
DEFAULT_OUTPUT = Path("web/data/papers.json")
DEFAULT_CONFERENCE_OUTPUT = Path("web/data/conference_papers.json")
DEFAULT_FIGURE_DIR = Path("web/data/figures")
RETAINED_MATCH_LEVELS = {"high", "medium"}
DEFAULT_MAX_NEW_PAPERS = 50
DEFAULT_MAX_STORED_PAPERS = 50
DEFAULT_MAX_NEW_CONFERENCE_PAPERS = 50
DEFAULT_MAX_STORED_CONFERENCE_PAPERS = 300
DEFAULT_MAX_DATA_BYTES = 8 * 1024 * 1024
DEFAULT_RECENT_HISTORY_DAYS = 45
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
DBLP_TRANSIENT_HTTP_CODES = {429, 502, 503, 504}
DEFAULT_SOURCE_TYPES = ["arxiv", "openalex", "crossref"]
FEED_NAMESPACES = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    description: str
    keywords: list[str]
    arxiv_categories: list[str]


@dataclass(frozen=True)
class SourceConfig:
    type: str
    name: str
    url: str = ""
    enabled: bool = True
    headers_env: str = ""
    bearer_token_env: str = ""


@dataclass(frozen=True)
class ConferenceSource:
    id: str
    name: str
    group: str
    dblp_toc_patterns: list[str]
    years: list[int]
    enabled: bool = True


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def json_size_bytes(data: dict[str, Any]) -> int:
    return len(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")) + 1


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


def env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name, "")
    if not value.strip():
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_topics(config: dict[str, Any]) -> list[Topic]:
    topics = []
    for item in config.get("topics", []):
        topic_id = item.get("id") or slugify(item.get("name", "topic"))
        topics.append(
            Topic(
                id=topic_id,
                name=item["name"],
                description=item.get("description", ""),
                keywords=[str(k) for k in item.get("keywords", [])],
                arxiv_categories=[str(c) for c in item.get("arxiv_categories", [])],
            )
        )
    if not topics:
        raise ValueError("No topics found in configuration.")
    return topics


def parse_sources(config: dict[str, Any]) -> list[SourceConfig]:
    configured = config.get("sources")
    if not configured:
        configured = [{"type": source_type} for source_type in env_list("PAPER_SOURCES", DEFAULT_SOURCE_TYPES)]

    sources = []
    for item in configured:
        if isinstance(item, str):
            item = {"type": item}
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("type") or "").strip().lower()
        if not source_type:
            continue
        if (
            source_type in {"semantic_scholar", "semanticscholar", "semantic-scholar"}
            and not env_flag("ENABLE_SEMANTIC_SCHOLAR", False)
        ):
            continue
        if item.get("enabled", True) is False:
            continue
        name = str(item.get("name") or source_type.replace("_", " ").title())
        sources.append(
            SourceConfig(
                type=source_type,
                name=name,
                url=str(item.get("url") or ""),
                enabled=bool(item.get("enabled", True)),
                headers_env=str(item.get("headers_env") or ""),
                bearer_token_env=str(item.get("bearer_token_env") or ""),
            )
        )
    return sources


def merge_venues(default_venues: list[dict[str, Any]], override_venues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for venue in [*default_venues, *override_venues]:
        if not isinstance(venue, dict):
            continue
        venue_id = str(venue.get("id") or slugify(str(venue.get("name", "venue"))))
        if venue_id not in by_id:
            order.append(venue_id)
            by_id[venue_id] = {"id": venue_id}
        by_id[venue_id].update(venue)
        by_id[venue_id]["id"] = venue_id
    return [by_id[venue_id] for venue_id in order]


def merge_config(default_config: dict[str, Any], override_config: dict[str, Any] | None) -> dict[str, Any]:
    if not override_config:
        return default_config

    merged = copy.deepcopy(default_config)
    for key, value in override_config.items():
        if key == "conference_sources" and isinstance(value, dict):
            default_sources = merged.get("conference_sources", {})
            if not isinstance(default_sources, dict):
                default_sources = {}
            merged_sources = copy.deepcopy(default_sources)
            include_defaults = bool(value.get("include_default_venues", True))
            default_venues = default_sources.get("venues", []) if include_defaults else []
            override_venues = value.get("venues", [])
            additional_venues = value.get("additional_venues", [])
            for source_key, source_value in value.items():
                if source_key not in {"venues", "additional_venues", "include_default_venues"}:
                    merged_sources[source_key] = source_value
            if "venues" in value or "additional_venues" in value or not include_defaults:
                merged_sources["venues"] = merge_venues(
                    default_venues if isinstance(default_venues, list) else [],
                    [
                        *(override_venues if isinstance(override_venues, list) else []),
                        *(additional_venues if isinstance(additional_venues, list) else []),
                    ],
                )
            merged["conference_sources"] = merged_sources
        else:
            merged[key] = value
    return merged


def parse_years(value: Any) -> list[int]:
    years = []
    if not isinstance(value, list):
        return years
    for item in value:
        try:
            years.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(years), reverse=True)


def default_conference_years(config: dict[str, Any], now: dt.datetime) -> list[int]:
    configured = parse_years(config.get("years"))
    if configured:
        return configured
    current_year = int(config.get("current_year") or now.year)
    lookback_years = max(1, int(config.get("lookback_years", 2) or 2))
    return [current_year - offset for offset in range(lookback_years)]


def parse_conference_sources(config: dict[str, Any], now: dt.datetime) -> list[ConferenceSource]:
    source_config = config.get("conference_sources", {})
    if not isinstance(source_config, dict) or not source_config.get("enabled", False):
        return []

    default_years = default_conference_years(source_config, now)
    sources = []
    for item in source_config.get("venues", []):
        if not isinstance(item, dict):
            continue
        patterns = item.get("dblp_toc_patterns") or item.get("dblp_toc_pattern") or []
        if isinstance(patterns, str):
            patterns = [patterns]
        years = parse_years(item.get("years")) or default_years
        source = ConferenceSource(
            id=str(item.get("id") or slugify(str(item.get("name", "venue")))),
            name=str(item.get("name") or item.get("id") or "Venue"),
            group=str(item.get("group") or "conference"),
            dblp_toc_patterns=[str(pattern) for pattern in patterns if str(pattern).strip()],
            years=years,
            enabled=bool(item.get("enabled", True)),
        )
        if source.enabled and source.dblp_toc_patterns:
            sources.append(source)
    return sources


def github_request(url: str, token: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "paper-daily-collector",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json_block(markdown: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", markdown, flags=re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))
    stripped = markdown.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return json.loads(stripped)
    return None


def load_issue_config(default_config: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    title = os.getenv("CONFIG_ISSUE_TITLE", "Research Interests")
    if not token or not repository:
        return default_config

    query = urllib.parse.urlencode({"state": "open", "per_page": "30"})
    url = f"https://api.github.com/repos/{repository}/issues?{query}"
    try:
        issues = github_request(url, token)
    except Exception as exc:
        print(f"Warning: cannot read GitHub issues, using config file: {exc}", file=sys.stderr)
        return default_config

    for issue in issues:
        if "pull_request" in issue:
            continue
        if issue.get("title", "").strip().lower() == title.lower():
            body = issue.get("body") or ""
            try:
                issue_config = extract_json_block(body)
            except json.JSONDecodeError as exc:
                print(f"Warning: config issue JSON is invalid, using config file: {exc}", file=sys.stderr)
                return default_config
            if issue_config and issue_config.get("topics"):
                return merge_config(default_config, issue_config)
    return default_config


def arxiv_query_for_topic(topic: Topic) -> str:
    keyword_terms = []
    for keyword in topic.keywords[:8]:
        escaped = keyword.replace('"', '\\"')
        keyword_terms.append(f'all:"{escaped}"')

    category_terms = [f"cat:{category}" for category in topic.arxiv_categories[:5]]
    query_mode = os.getenv("ARXIV_QUERY_MODE", "keyword").strip().lower()
    if query_mode == "keyword":
        if keyword_terms:
            return "(" + " OR ".join(keyword_terms) + ")"
        if category_terms:
            return "(" + " OR ".join(category_terms) + ")"
        return f'all:"{topic.name}"'

    if query_mode == "strict":
        parts = []
        if keyword_terms:
            parts.append("(" + " OR ".join(keyword_terms) + ")")
        if category_terms:
            parts.append("(" + " OR ".join(category_terms) + ")")
        return " AND ".join(parts) if parts else f'all:"{topic.name}"'

    terms = [*keyword_terms, *category_terms]
    if terms:
        return "(" + " OR ".join(terms) + ")"

    parts = []
    if keyword_terms:
        parts.append("(" + " OR ".join(keyword_terms) + ")")
    if category_terms:
        parts.append("(" + " OR ".join(category_terms) + ")")
    return " AND ".join(parts) if parts else f'all:"{topic.name}"'


def arxiv_category_query_for_topic(topic: Topic) -> str:
    category_terms = [f"cat:{category}" for category in topic.arxiv_categories[:5]]
    if category_terms:
        return "(" + " OR ".join(category_terms) + ")"
    return arxiv_query_for_topic(topic)


def topic_text_query(topic: Topic, limit: int = 6) -> str:
    terms = topic.keywords[:limit] or [topic.name]
    return " OR ".join(terms)


def topic_plain_query(topic: Topic, limit: int = 6) -> str:
    return " ".join(topic.keywords[:limit]) or topic.name


def html_to_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return normalize_space(html.unescape(without_tags))


def date_to_iso(value: str | int | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, int):
        return f"{value:04d}-01-01T00:00:00+00:00"
    parsed = parse_datetime(str(value))
    if parsed:
        return parsed.isoformat()
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00+00:00"
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-01-01T00:00:00+00:00"
    return text


def request_json(url: str, headers: dict[str, str] | None = None, timeout: float = 60) -> Any:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "paper-daily-collector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_bytes(url: str, headers: dict[str, str] | None = None, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "paper-daily-collector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def source_request_headers(source: SourceConfig) -> dict[str, str]:
    headers = {"User-Agent": "paper-daily-collector/1.0"}
    if source.headers_env:
        raw_headers = os.getenv(source.headers_env, "")
        if raw_headers:
            try:
                configured_headers = json.loads(raw_headers)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source.headers_env} must contain a JSON object of HTTP headers") from exc
            if not isinstance(configured_headers, dict):
                raise ValueError(f"{source.headers_env} must contain a JSON object of HTTP headers")
            headers.update({str(key): str(value) for key, value in configured_headers.items()})
    if source.bearer_token_env:
        token = os.getenv(source.bearer_token_env, "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def arxiv_retry_wait_seconds(exc: Exception, attempt: int) -> float:
    min_wait = float(os.getenv("ARXIV_RETRY_MIN_SECONDS", "45"))
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return max(min_wait, float(retry_after))
    base = float(os.getenv("ARXIV_RETRY_BASE_SECONDS", "45"))
    cap = float(os.getenv("ARXIV_RETRY_MAX_SECONDS", "180"))
    return max(min_wait, min(cap, base * (2**attempt)))


def is_retryable_arxiv_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    return isinstance(exc, (TimeoutError, urllib.error.URLError, OSError))


def should_retry_arxiv_error(exc: Exception) -> bool:
    if not is_retryable_arxiv_error(exc):
        return False
    if isinstance(exc, urllib.error.HTTPError) and exc.code in {429, 503}:
        return env_flag("ARXIV_RETRY_THROTTLED", False)
    return True


def should_stop_arxiv_fetches(exc: Exception) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code in {429, 503}


def parse_arxiv_entries(xml_data: bytes, seed_topic: str = "") -> list[dict[str, Any]]:
    root = ET.fromstring(xml_data)
    papers = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        paper_id = entry.findtext("atom:id", default="", namespaces=ARXIV_NS).strip()
        title = normalize_space(entry.findtext("atom:title", default="", namespaces=ARXIV_NS))
        summary = normalize_space(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS))
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS)
        updated = entry.findtext("atom:updated", default="", namespaces=ARXIV_NS)
        authors = [
            normalize_space(author.findtext("atom:name", default="", namespaces=ARXIV_NS))
            for author in entry.findall("atom:author", ARXIV_NS)
        ]
        categories = [
            category.attrib.get("term", "")
            for category in entry.findall("atom:category", ARXIV_NS)
            if category.attrib.get("term")
        ]
        pdf_url = ""
        for link in entry.findall("atom:link", ARXIV_NS):
            if link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href", "")
                break
        papers.append(
            {
                "id": paper_id.rsplit("/", 1)[-1],
                "source": "arXiv",
                "title": title,
                "authors": [a for a in authors if a],
                "summary": summary,
                "published": published,
                "updated": updated,
                "paper_url": paper_id,
                "pdf_url": pdf_url or paper_id.replace("/abs/", "/pdf/"),
                "categories": categories,
                "seed_topic": seed_topic,
            }
        )
    return papers


def fetch_arxiv_query(search_query: str, max_results: int, sort_by: str, sort_order: str, label: str) -> list[dict[str, Any]]:
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(max_results),
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    retry_count = max(1, int(os.getenv("ARXIV_RETRIES", "4")))
    timeout_seconds = float(os.getenv("ARXIV_TIMEOUT_SECONDS", "90"))
    last_error: Exception | None = None
    for attempt in range(retry_count):
        req = urllib.request.Request(url, headers={"User-Agent": "paper-daily-collector/1.0 (+https://github.com/Futuresxy/paper-daily)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                xml_data = resp.read()
            break
        except Exception as exc:
            last_error = exc
            if not should_retry_arxiv_error(exc) or attempt == retry_count - 1:
                raise
            wait_seconds = arxiv_retry_wait_seconds(exc, attempt)
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                print(f"arXiv rate limited {label}, retrying in {wait_seconds:.0f}s", flush=True)
            else:
                print(f"arXiv temporary error for {label}: {exc}; retrying in {wait_seconds:.0f}s", flush=True)
            time.sleep(wait_seconds)
    else:
        raise RuntimeError(f"arXiv request failed: {last_error}")
    return parse_arxiv_entries(xml_data)


def fetch_arxiv(topic: Topic, max_results: int) -> list[dict[str, Any]]:
    sort_by = os.getenv("ARXIV_SORT_BY", "lastUpdatedDate").strip() or "lastUpdatedDate"
    papers = fetch_arxiv_query(
        arxiv_query_for_topic(topic),
        max_results,
        sort_by=sort_by,
        sort_order="descending",
        label=topic.name,
    )
    if env_flag("ARXIV_EXPAND_CATEGORY_SEARCH", False) and topic.arxiv_categories:
        in_topic_delay = float(os.getenv("ARXIV_IN_TOPIC_DELAY_SECONDS", "3"))
        if in_topic_delay > 0:
            time.sleep(in_topic_delay)
        category_max_results = max(1, int(os.getenv("ARXIV_CATEGORY_MAX_RESULTS", str(max_results))))
        category_papers = fetch_arxiv_query(
            arxiv_category_query_for_topic(topic),
            category_max_results,
            sort_by=sort_by,
            sort_order="descending",
            label=f"{topic.name} categories",
        )
        papers = dedupe_papers([*papers, *category_papers])
    for paper in papers:
        paper["seed_topic"] = topic.id
    return papers


def title_match_key(title: str) -> str:
    text = html.unescape(title).lower()
    text = re.sub(r"\barxiv:\d{4}\.\d+(v\d+)?\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return normalize_space(text)


def titles_match(left: str, right: str) -> bool:
    left_key = title_match_key(left)
    right_key = title_match_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    return difflib.SequenceMatcher(None, left_key, right_key).ratio() >= env_float("ARXIV_TITLE_MATCH_RATIO", 0.90)


def arxiv_title_query(title: str) -> str:
    safe_title = normalize_space(title).replace('"', " ")
    if not safe_title:
        return 'all:""'
    # Exact title search first; arXiv still returns close matches when punctuation differs.
    return f'ti:"{safe_title}"'


def find_arxiv_by_title(title: str, max_results: int = 5) -> dict[str, Any] | None:
    papers = fetch_arxiv_query(
        arxiv_title_query(title),
        max_results=max(1, max_results),
        sort_by="relevance",
        sort_order="descending",
        label=f"title:{title[:80]}",
    )
    for candidate in papers:
        if titles_match(title, str(candidate.get("title") or "")) and has_meaningful_summary(candidate):
            return candidate
    return None


def semantic_scholar_paper_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    paper_id = str(item.get("paperId") or "")
    title = normalize_space(str(item.get("title") or ""))
    if not paper_id or not title:
        return None
    raw_authors = item.get("authors") or []
    raw_fields = item.get("fieldsOfStudy") or []
    authors = [str(author.get("name") or "") for author in raw_authors[:12] if isinstance(author, dict)]
    categories = [str(value) for value in raw_fields if value]
    venue = str(item.get("venue") or "")
    if venue:
        categories.append(venue)
    pdf_url = str((item.get("openAccessPdf") or {}).get("url") or "")
    return {
        "id": f"s2:{paper_id}",
        "source": "Semantic Scholar",
        "title": title,
        "authors": [author for author in authors if author],
        "summary": normalize_space(str(item.get("abstract") or "")),
        "published": date_to_iso(item.get("publicationDate") or item.get("year")),
        "updated": "",
        "paper_url": str(item.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"),
        "pdf_url": pdf_url,
        "categories": categories[:8],
    }


def find_semantic_scholar_by_title(title: str, max_results: int = 5) -> dict[str, Any] | None:
    params = {
        "query": title,
        "limit": str(min(max(1, max_results), 100)),
        "fields": "paperId,title,abstract,authors,year,publicationDate,url,openAccessPdf,venue,externalIds,fieldsOfStudy",
    }
    headers = {"User-Agent": "paper-daily-collector/1.0"}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key
    url = f"{SEMANTIC_SCHOLAR_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, headers=headers, timeout=float(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", "60")))
    for item in data.get("data") or []:
        candidate = semantic_scholar_paper_from_item(item)
        if candidate and titles_match(title, candidate["title"]) and has_meaningful_summary(candidate):
            return candidate
    return None


def openalex_paper_from_work(work: dict[str, Any], source_name: str = "OpenAlex") -> dict[str, Any] | None:
    title = normalize_space(str(work.get("title") or ""))
    work_id = str(work.get("id") or work.get("doi") or title)
    if not title or not work_id:
        return None
    locations = work.get("locations") or []
    primary = work.get("primary_location") or {}
    best_oa = work.get("best_oa_location") or {}
    pdf_url = (
        primary.get("pdf_url")
        or best_oa.get("pdf_url")
        or next((location.get("pdf_url") for location in locations if location.get("pdf_url")), "")
    )
    authors = [
        str((authorship.get("author") or {}).get("display_name") or "")
        for authorship in work.get("authorships", [])
    ]
    concepts = [
        str(concept.get("display_name") or "")
        for concept in work.get("concepts", [])[:8]
        if concept.get("display_name")
    ]
    return {
        "id": f"openalex:{work_id.rsplit('/', 1)[-1]}",
        "source": source_name,
        "title": title,
        "authors": [author for author in authors if author],
        "summary": normalize_space(openalex_abstract_text(work)),
        "published": date_to_iso(work.get("publication_date") or work.get("publication_year")),
        "updated": "",
        "paper_url": str(work.get("doi") or work.get("id") or ""),
        "pdf_url": str(pdf_url or ""),
        "categories": concepts,
    }


def find_openalex_by_title(title: str, max_results: int = 5) -> dict[str, Any] | None:
    params = {
        "search": title,
        "per-page": str(min(max(1, max_results), 25)),
    }
    mailto = os.getenv("CONTACT_EMAIL") or os.getenv("OPENALEX_EMAIL")
    if mailto:
        params["mailto"] = mailto
    url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, timeout=float(os.getenv("OPENALEX_TIMEOUT_SECONDS", "60")))
    for work in data.get("results", []):
        candidate = openalex_paper_from_work(work)
        if candidate and titles_match(title, candidate["title"]) and has_meaningful_summary(candidate):
            return candidate
    return None


def crossref_paper_from_item(item: dict[str, Any], source_name: str = "Crossref") -> dict[str, Any] | None:
    title = normalize_space(" ".join(str(part) for part in item.get("title", []) if part))
    doi = str(item.get("DOI") or "")
    paper_url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
    if not title or not (doi or paper_url):
        return None
    authors = []
    for author in item.get("author", [])[:12]:
        name = normalize_space(f"{author.get('given', '')} {author.get('family', '')}")
        if name:
            authors.append(name)
    pdf_url = ""
    for link in item.get("link", []):
        if "pdf" in str(link.get("content-type", "")).lower() and link.get("URL"):
            pdf_url = str(link.get("URL"))
            break
    return {
        "id": f"crossref:{doi or slugify(title)}",
        "source": source_name,
        "title": title,
        "authors": authors,
        "summary": html_to_text(str(item.get("abstract") or "")),
        "published": crossref_date(item),
        "updated": "",
        "paper_url": paper_url,
        "pdf_url": pdf_url,
        "categories": [str(subject) for subject in item.get("subject", [])[:8]],
    }


def find_crossref_by_title(title: str, max_results: int = 5) -> dict[str, Any] | None:
    params = {
        "query.title": title,
        "rows": str(min(max(1, max_results), 20)),
        "sort": "score",
        "order": "desc",
    }
    mailto = os.getenv("CONTACT_EMAIL") or os.getenv("CROSSREF_EMAIL")
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": f"paper-daily-collector/1.0 (mailto:{mailto or 'unknown@example.com'})"}
    url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, headers=headers, timeout=float(os.getenv("CROSSREF_TIMEOUT_SECONDS", "60")))
    for item in (data.get("message") or {}).get("items", []):
        candidate = crossref_paper_from_item(item)
        if candidate and titles_match(title, candidate["title"]) and has_meaningful_summary(candidate):
            return candidate
    return None


def find_conference_abstract_by_title(title: str, max_results: int = 5) -> dict[str, Any] | None:
    finders = {
        "arxiv": find_arxiv_by_title,
        "semantic_scholar": find_semantic_scholar_by_title,
        "semanticscholar": find_semantic_scholar_by_title,
        "openalex": find_openalex_by_title,
        "crossref": find_crossref_by_title,
    }
    for source_type in conference_abstract_sources():
        finder = finders.get(source_type.strip().lower())
        if not finder:
            continue
        try:
            candidate = finder(title, max_results=max_results)
        except Exception as exc:
            print(f"Warning: {source_type} title enrichment failed for {title[:80]}: {exc}", file=sys.stderr)
            continue
        if candidate and has_meaningful_summary(candidate):
            return candidate
    return None


def conference_abstract_sources() -> list[str]:
    sources = env_list("CONFERENCE_ABSTRACT_SOURCES", ["arxiv", "openalex", "crossref"])
    if env_flag("ENABLE_SEMANTIC_SCHOLAR", False):
        return sources
    return [
        source
        for source in sources
        if source.strip().lower() not in {"semantic_scholar", "semanticscholar", "semantic-scholar"}
    ]


def enrich_conference_paper_from_arxiv(paper: dict[str, Any], arxiv_paper: dict[str, Any]) -> bool:
    return enrich_conference_paper_from_candidate(paper, arxiv_paper, "arXiv")


def enrich_conference_paper_from_candidate(
    paper: dict[str, Any],
    candidate: dict[str, Any],
    abstract_source: str | None = None,
) -> bool:
    if not has_meaningful_summary(candidate):
        return False
    source = abstract_source or str(candidate.get("source") or "external")
    paper["summary"] = candidate["summary"]
    paper["abstract_source"] = source
    paper["enriched"] = True
    paper["abstract_source_id"] = candidate.get("id", "")
    paper["abstract_source_url"] = candidate.get("paper_url", "")
    if source.lower() == "arxiv":
        paper["arxiv_id"] = candidate.get("id", "")
        paper["arxiv_url"] = candidate.get("paper_url", "")
    paper["source"] = f"{paper.get('source', 'DBLP')} + {source}"
    if candidate.get("paper_url"):
        paper["paper_url"] = candidate["paper_url"]
    if candidate.get("pdf_url"):
        paper["pdf_url"] = candidate["pdf_url"]
    if candidate.get("authors"):
        paper["authors"] = candidate["authors"]
    categories = list(dict.fromkeys([*paper.get("categories", []), *candidate.get("categories", [])]))
    paper["categories"] = [category for category in categories if category]
    return True


def dblp_retry_wait_seconds(exc: Exception, attempt: int) -> float:
    min_wait = float(os.getenv("DBLP_RETRY_MIN_SECONDS", "5"))
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return max(min_wait, float(retry_after))
    base = float(os.getenv("DBLP_RETRY_BASE_SECONDS", "5"))
    cap = float(os.getenv("DBLP_RETRY_MAX_SECONDS", "60"))
    return max(min_wait, min(cap, base * (2**attempt)))


def is_retryable_dblp_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in DBLP_TRANSIENT_HTTP_CODES
    return isinstance(exc, (TimeoutError, urllib.error.URLError, OSError, http.client.RemoteDisconnected))


def fetch_json_url(url: str, user_agent: str, timeout_seconds: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_dblp_json(query: str, max_results: int) -> dict[str, Any]:
    params = {
        "format": "json",
        "h": str(max_results),
        "q": query,
    }
    url = f"{DBLP_API_URL}?{urllib.parse.urlencode(params)}"
    retry_count = max(1, int(os.getenv("DBLP_RETRIES", "3")))
    timeout_seconds = float(os.getenv("DBLP_TIMEOUT_SECONDS", "45"))
    last_error: Exception | None = None
    for attempt in range(retry_count):
        try:
            return fetch_json_url(url, "paper-daily-collector/1.0 (+https://github.com/Futuresxy/paper-daily)", timeout_seconds)
        except Exception as exc:
            last_error = exc
            if not is_retryable_dblp_error(exc) or attempt == retry_count - 1:
                raise
            wait_seconds = dblp_retry_wait_seconds(exc, attempt)
            print(f"DBLP temporary error for query {query}: {exc}; retrying in {wait_seconds:.0f}s", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"DBLP request failed: {last_error}")


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_dblp_authors(info: dict[str, Any]) -> list[str]:
    authors = info.get("authors", {}).get("author", []) if isinstance(info.get("authors"), dict) else []
    names = []
    for author in ensure_list(authors):
        if isinstance(author, dict):
            name = author.get("text", "")
        else:
            name = str(author)
        name = normalize_space(str(name))
        if name:
            names.append(name)
    return names


def parse_dblp_hits(data: dict[str, Any], source: ConferenceSource, year: int, toc_key: str) -> list[dict[str, Any]]:
    hits_data = data.get("result", {}).get("hits", {}).get("hit", [])
    papers = []
    for hit in ensure_list(hits_data):
        if not isinstance(hit, dict):
            continue
        info = hit.get("info", {})
        if not isinstance(info, dict):
            continue
        key = str(info.get("key") or "")
        title = normalize_space(str(info.get("title") or ""))
        if not key or not title or key.endswith(f"/{year}"):
            continue
        venue = str(info.get("venue") or source.name)
        pages = str(info.get("pages") or "").strip()
        doi = str(info.get("doi") or "").strip()
        ee = str(info.get("ee") or "").strip()
        url = str(info.get("url") or "").strip()
        paper_url = url or f"https://dblp.org/rec/{key}"
        summary_parts = [f"DBLP 题录：{source.name} {year} 会议论文。"]
        if pages:
            summary_parts.append(f"页码：{pages}。")
        if doi:
            summary_parts.append(f"DOI：{doi}。")
        papers.append(
            {
                "id": f"dblp:{key}",
                "source": f"DBLP · {source.name}",
                "source_type": "conference",
                "title": html.unescape(title).rstrip("."),
                "authors": parse_dblp_authors(info),
                "summary": " ".join(summary_parts),
                "published": f"{year}-01-01T00:00:00+00:00",
                "updated": f"{year}-01-01T00:00:00+00:00",
                "paper_url": paper_url,
                "pdf_url": ee or paper_url,
                "categories": [source.name, source.group, str(year)],
                "venue": venue,
                "conference": {
                    "id": source.id,
                    "name": source.name,
                    "group": source.group,
                    "year": year,
                    "dblp_key": key,
                    "dblp_toc": toc_key,
                    "doi": doi,
                    "ee": ee,
                    "pages": pages,
                },
            }
        )
    return papers


def strip_html_tags(value: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", "", html.unescape(value)))


def dblp_html_url_for_toc(toc_key: str) -> str:
    path = toc_key
    if path.endswith(".bht"):
        path = path[:-4] + ".html"
    elif not path.endswith(".html"):
        path += ".html"
    return "http://dblp.org/" + path.lstrip("/")


def conference_paper_from_dblp_html_chunk(
    key: str,
    chunk: str,
    source: ConferenceSource,
    year: int,
    toc_key: str,
) -> dict[str, Any] | None:
    title_match = re.search(r'<span class="title"[^>]*>(.*?)</span>', chunk, flags=re.S)
    if not title_match:
        return None
    title = strip_html_tags(title_match.group(1)).rstrip(".")
    if not title or title.lower().startswith("proceedings of"):
        return None

    authors = [
        strip_html_tags(author)
        for author in re.findall(r'<span itemprop="name" title="([^"]+)">', chunk)
    ]
    pages_match = re.search(r'<span itemprop="pagination">(.*?)</span>', chunk, flags=re.S)
    pages = strip_html_tags(pages_match.group(1)) if pages_match else ""
    ee_match = re.search(r'<li class="ee">\s*<a href="([^"]+)"', chunk, flags=re.S)
    ee = html.unescape(ee_match.group(1)) if ee_match else ""
    paper_url = f"https://dblp.org/rec/{key}"
    summary_parts = [f"DBLP 题录：{source.name} {year} 会议论文。"]
    if pages:
        summary_parts.append(f"页码：{pages}。")
    return {
        "id": f"dblp:{key}",
        "source": f"DBLP · {source.name}",
        "source_type": "conference",
        "title": title,
        "authors": authors,
        "summary": " ".join(summary_parts),
        "published": f"{year}-01-01T00:00:00+00:00",
        "updated": f"{year}-01-01T00:00:00+00:00",
        "paper_url": paper_url,
        "pdf_url": ee or paper_url,
        "categories": [source.name, source.group, str(year)],
        "venue": source.name,
        "conference": {
            "id": source.id,
            "name": source.name,
            "group": source.group,
            "year": year,
            "dblp_key": key,
            "dblp_toc": toc_key,
            "doi": "",
            "ee": ee,
            "pages": pages,
        },
    }


def parse_dblp_html_toc(html_text: str, source: ConferenceSource, year: int, toc_key: str) -> list[dict[str, Any]]:
    starts = list(re.finditer(r'<li class="entry inproceedings" id="([^"]+)"', html_text))
    papers = []
    for index, match in enumerate(starts):
        key = html.unescape(match.group(1))
        if key.endswith(f"/{year}"):
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        paper = conference_paper_from_dblp_html_chunk(key, html_text[match.start():end], source, year, toc_key)
        if paper:
            papers.append(paper)
    return papers


def fetch_dblp_html_toc(toc_key: str, source: ConferenceSource, year: int) -> list[dict[str, Any]]:
    url = dblp_html_url_for_toc(toc_key)
    timeout_seconds = float(os.getenv("DBLP_TIMEOUT_SECONDS", "45"))
    retry_count = max(1, int(os.getenv("DBLP_RETRIES", "3")))
    last_error: Exception | None = None
    for attempt in range(retry_count):
        req = urllib.request.Request(url, headers={"User-Agent": "paper-daily-collector/1.0 (+https://github.com/Futuresxy/paper-daily)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                html_text = resp.read().decode("utf-8", "replace")
            return parse_dblp_html_toc(html_text, source, year, toc_key)
        except Exception as exc:
            last_error = exc
            if not is_retryable_dblp_error(exc) or attempt == retry_count - 1:
                raise
            wait_seconds = dblp_retry_wait_seconds(exc, attempt)
            print(f"DBLP temporary HTML error for {source.name} {year}: {exc}; retrying in {wait_seconds:.0f}s", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"DBLP HTML request failed: {last_error}")


def fetch_dblp_conference(source: ConferenceSource, max_results: int) -> list[dict[str, Any]]:
    papers = []
    errors = []
    request_count = 0
    pattern_delay_seconds = float(os.getenv("DBLP_PATTERN_DELAY_SECONDS", "3"))
    for year in source.years:
        for pattern_index, pattern in enumerate(source.dblp_toc_patterns):
            if request_count:
                time.sleep(pattern_delay_seconds)
            toc_key = pattern.format(year=year)
            query = f"toc:{toc_key}:"
            request_count += 1
            try:
                data = fetch_dblp_json(query, max_results)
            except Exception as exc:
                try:
                    fallback_papers = fetch_dblp_html_toc(toc_key, source, year)
                except Exception as fallback_exc:
                    errors.append(fallback_exc)
                    print(
                        f"Warning: DBLP TOC request failed for {source.name} {year} pattern {pattern_index + 1}: {exc}; HTML fallback failed: {fallback_exc}",
                        file=sys.stderr,
                    )
                    continue
                papers.extend(fallback_papers[:max_results])
                continue
            papers.extend(parse_dblp_hits(data, source, year, toc_key))
    if not papers and errors:
        raise errors[-1]
    return dedupe_papers(papers)


def openalex_abstract_text(work: dict[str, Any]) -> str:
    inverted = work.get("abstract_inverted_index")
    if not isinstance(inverted, dict):
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                words.append((position, str(word)))
    return " ".join(word for _, word in sorted(words))


def fetch_openalex(topic: Topic, max_results: int, source: SourceConfig) -> list[dict[str, Any]]:
    params = {
        "search": topic_plain_query(topic),
        "per-page": str(max_results),
        "sort": "publication_date:desc",
    }
    mailto = os.getenv("CONTACT_EMAIL") or os.getenv("OPENALEX_EMAIL")
    if mailto:
        params["mailto"] = mailto
    url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, timeout=float(os.getenv("OPENALEX_TIMEOUT_SECONDS", "60")))
    papers = []
    for work in data.get("results", []):
        locations = work.get("locations") or []
        primary = work.get("primary_location") or {}
        best_oa = work.get("best_oa_location") or {}
        pdf_url = (
            primary.get("pdf_url")
            or best_oa.get("pdf_url")
            or next((location.get("pdf_url") for location in locations if location.get("pdf_url")), "")
        )
        authors = [
            str((authorship.get("author") or {}).get("display_name") or "")
            for authorship in work.get("authorships", [])
        ]
        concepts = [
            str(concept.get("display_name") or "")
            for concept in work.get("concepts", [])[:8]
            if concept.get("display_name")
        ]
        work_id = str(work.get("id") or work.get("doi") or work.get("title") or "")
        if not work_id:
            continue
        papers.append(
            {
                "id": f"openalex:{work_id.rsplit('/', 1)[-1]}",
                "source": source.name,
                "title": normalize_space(str(work.get("title") or "")),
                "authors": [author for author in authors if author],
                "summary": normalize_space(openalex_abstract_text(work)),
                "published": date_to_iso(work.get("publication_date") or work.get("publication_year")),
                "updated": "",
                "paper_url": str(work.get("doi") or work.get("id") or ""),
                "pdf_url": str(pdf_url or ""),
                "categories": concepts,
                "seed_topic": topic.id,
            }
        )
    return papers


def crossref_date(item: dict[str, Any]) -> str:
    for field in ("published-print", "published-online", "published", "created", "issued"):
        date_parts = (item.get(field) or {}).get("date-parts") or []
        if date_parts and date_parts[0]:
            parts = list(date_parts[0])
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            day = int(parts[2]) if len(parts) > 2 else 1
            return dt.datetime(year, month, day, tzinfo=dt.timezone.utc).isoformat()
    return ""


def fetch_crossref(topic: Topic, max_results: int, source: SourceConfig) -> list[dict[str, Any]]:
    params = {
        "query.bibliographic": topic_plain_query(topic),
        "rows": str(max_results),
        "sort": "published",
        "order": "desc",
    }
    mailto = os.getenv("CONTACT_EMAIL") or os.getenv("CROSSREF_EMAIL")
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": f"paper-daily-collector/1.0 (mailto:{mailto or 'unknown@example.com'})"}
    url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, headers=headers, timeout=float(os.getenv("CROSSREF_TIMEOUT_SECONDS", "60")))
    papers = []
    for item in (data.get("message") or {}).get("items", []):
        title = normalize_space(" ".join(str(part) for part in item.get("title", []) if part))
        doi = str(item.get("DOI") or "")
        paper_url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
        if not title or not (doi or paper_url):
            continue
        authors = []
        for author in item.get("author", [])[:12]:
            name = normalize_space(f"{author.get('given', '')} {author.get('family', '')}")
            if name:
                authors.append(name)
        subjects = [str(subject) for subject in item.get("subject", [])[:8]]
        papers.append(
            {
                "id": f"crossref:{doi or slugify(title)}",
                "source": source.name,
                "title": title,
                "authors": authors,
                "summary": html_to_text(str(item.get("abstract") or "")),
                "published": crossref_date(item),
                "updated": "",
                "paper_url": paper_url,
                "pdf_url": "",
                "categories": subjects,
                "seed_topic": topic.id,
            }
        )
    return papers


def fetch_semantic_scholar(topic: Topic, max_results: int, source: SourceConfig) -> list[dict[str, Any]]:
    params = {
        "query": topic_plain_query(topic),
        "limit": str(min(max_results, 100)),
        "fields": "paperId,title,abstract,authors,year,publicationDate,url,openAccessPdf,venue,externalIds,fieldsOfStudy",
    }
    headers = {"User-Agent": "paper-daily-collector/1.0"}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key
    url = f"{SEMANTIC_SCHOLAR_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url, headers=headers, timeout=float(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", "60")))
    papers = []
    for item in data.get("data") or []:
        candidate = semantic_scholar_paper_from_item(item)
        if not candidate:
            continue
        candidate["source"] = source.name
        candidate["seed_topic"] = topic.id
        papers.append(candidate)
    return papers


def fetch_google_scholar_serpapi(topic: Topic, max_results: int, source: SourceConfig) -> list[dict[str, Any]]:
    api_key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_API_KEY is required for google_scholar_serpapi source")
    params = {
        "engine": "google_scholar",
        "q": topic_plain_query(topic),
        "num": str(min(max_results, 20)),
        "api_key": api_key,
    }
    data = request_json(
        f"{SERPAPI_SEARCH_URL}?{urllib.parse.urlencode(params)}",
        timeout=float(os.getenv("SERPAPI_TIMEOUT_SECONDS", "90")),
    )
    papers = []
    for item in data.get("organic_results", []):
        title = normalize_space(str(item.get("title") or ""))
        paper_url = str(item.get("link") or "")
        if not title or not paper_url:
            continue
        publication = item.get("publication_info") or {}
        publication_summary = str(publication.get("summary") or "")
        year_match = re.search(r"\b(19|20)\d{2}\b", publication_summary)
        resources = item.get("resources") or []
        pdf_url = next((str(resource.get("link")) for resource in resources if str(resource.get("file_format", "")).upper() == "PDF"), "")
        papers.append(
            {
                "id": f"google-scholar:{slugify(paper_url or title)}",
                "source": source.name,
                "title": title,
                "authors": [],
                "summary": normalize_space(" ".join([str(item.get("snippet") or ""), publication_summary])),
                "published": date_to_iso(year_match.group(0) if year_match else ""),
                "updated": "",
                "paper_url": paper_url,
                "pdf_url": pdf_url,
                "categories": ["Google Scholar"],
                "seed_topic": topic.id,
            }
        )
    return papers


def link_from_atom(entry: ET.Element) -> str:
    alternate = ""
    for link in entry.findall("atom:link", FEED_NAMESPACES):
        href = link.attrib.get("href", "")
        rel = link.attrib.get("rel", "alternate")
        if rel == "alternate" and href:
            return href
        if href and not alternate:
            alternate = href
    return alternate


def fetch_feed(source: SourceConfig, max_results: int) -> list[dict[str, Any]]:
    if not source.url:
        return []
    xml_data = request_bytes(
        source.url,
        headers=source_request_headers(source),
        timeout=float(os.getenv("FEED_TIMEOUT_SECONDS", "60")),
    )
    root = ET.fromstring(xml_data)
    papers = []
    atom_entries = root.findall("atom:entry", FEED_NAMESPACES)
    if root.tag.endswith("entry"):
        atom_entries = [root]
    for entry in atom_entries[:max_results]:
        title = normalize_space(entry.findtext("atom:title", default="", namespaces=FEED_NAMESPACES))
        summary = entry.findtext("atom:summary", default="", namespaces=FEED_NAMESPACES) or entry.findtext("atom:content", default="", namespaces=FEED_NAMESPACES)
        paper_url = link_from_atom(entry)
        paper_id = entry.findtext("atom:id", default=paper_url, namespaces=FEED_NAMESPACES)
        authors = [
            normalize_space(author.findtext("atom:name", default="", namespaces=FEED_NAMESPACES))
            for author in entry.findall("atom:author", FEED_NAMESPACES)
        ]
        categories = [category.attrib.get("term", "") for category in entry.findall("atom:category", FEED_NAMESPACES)]
        papers.append(
            {
                "id": f"feed:{slugify(source.name)}:{paper_id or paper_url or slugify(title)}",
                "source": source.name,
                "title": title,
                "authors": [author for author in authors if author],
                "summary": html_to_text(summary or ""),
                "published": date_to_iso(entry.findtext("atom:published", default="", namespaces=FEED_NAMESPACES)),
                "updated": date_to_iso(entry.findtext("atom:updated", default="", namespaces=FEED_NAMESPACES)),
                "paper_url": paper_url,
                "pdf_url": "",
                "categories": [category for category in categories if category],
                "seed_topic": "",
            }
        )

    for item in root.findall(".//channel/item")[:max_results]:
        title = normalize_space(item.findtext("title", default=""))
        paper_url = normalize_space(item.findtext("link", default=""))
        guid = normalize_space(item.findtext("guid", default=paper_url))
        papers.append(
            {
                "id": f"feed:{slugify(source.name)}:{guid or paper_url or slugify(title)}",
                "source": source.name,
                "title": title,
                "authors": [],
                "summary": html_to_text(item.findtext("description", default="")),
                "published": date_to_iso(item.findtext("pubDate", default="")),
                "updated": "",
                "paper_url": paper_url,
                "pdf_url": "",
                "categories": [],
                "seed_topic": "",
            }
        )
    return [paper for paper in papers if paper.get("title")]


def fetch_source_topic(source: SourceConfig, topic: Topic, max_results: int) -> list[dict[str, Any]]:
    if source.type == "arxiv":
        return fetch_arxiv(topic, max_results)
    if source.type == "openalex":
        return fetch_openalex(topic, max_results, source)
    if source.type == "crossref":
        return fetch_crossref(topic, max_results, source)
    if source.type == "semantic_scholar":
        return fetch_semantic_scholar(topic, max_results, source)
    if source.type == "google_scholar_serpapi":
        return fetch_google_scholar_serpapi(topic, max_results, source)
    raise ValueError(f"Unsupported topic source type: {source.type}")


def is_feed_source(source: SourceConfig) -> bool:
    return source.type in {"feed", "rss", "atom"}


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def paper_datetime(paper: dict[str, Any]) -> dt.datetime:
    for field in ("published", "updated", "last_seen_at", "first_seen_at"):
        parsed = parse_datetime(str(paper.get(field, "")))
        if parsed:
            return parsed
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def paper_activity_datetime(paper: dict[str, Any]) -> dt.datetime:
    for field in ("updated", "published", "last_seen_at", "first_seen_at"):
        parsed = parse_datetime(str(paper.get(field, "")))
        if parsed:
            return parsed
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def collection_cutoff(
    existing_payload: dict[str, Any],
    now: dt.datetime,
    days: int,
    incremental_since_last_run: bool,
) -> tuple[dt.datetime, str]:
    if incremental_since_last_run:
        previous_run = parse_datetime(
            str(existing_payload.get("generated_at_iso") or existing_payload.get("generated_at") or "")
        )
        if previous_run:
            return previous_run, "incremental"
    return now - dt.timedelta(days=max(0, days)), "lookback"


def keyword_score(topic: Topic, paper: dict[str, Any]) -> tuple[float, list[str]]:
    haystack = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
    hits = []
    weighted = 0.0
    for keyword in topic.keywords:
        normalized = keyword.lower()
        if normalized in haystack:
            hits.append(keyword)
            weighted += min(1.0, max(0.35, len(normalized.split()) / 5))
    score = min(1.0, weighted / max(2.0, min(5.0, len(topic.keywords) / 2)))
    return score, hits[:6]


def category_score(topic: Topic, paper: dict[str, Any]) -> float:
    paper_categories = set(paper.get("categories", []))
    topic_categories = set(topic.arxiv_categories)
    if not paper_categories or not topic_categories:
        return 0.0
    return len(paper_categories & topic_categories) / len(topic_categories)


def lexical_overlap_score(topic: Topic, paper: dict[str, Any]) -> float:
    topic_terms = set(re.findall(r"[a-zA-Z0-9]+", f"{topic.description} {' '.join(topic.keywords)}".lower()))
    paper_terms = set(re.findall(r"[a-zA-Z0-9]+", f"{paper.get('title', '')} {paper.get('summary', '')}".lower()))
    if not topic_terms or not paper_terms:
        return 0.0
    overlap = topic_terms & paper_terms
    return min(1.0, len(overlap) / max(8, len(topic_terms) * 0.18))


def match_level(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.42:
        return "medium"
    return "low"


def score_paper(topic: Topic, paper: dict[str, Any]) -> dict[str, Any]:
    k_score, hits = keyword_score(topic, paper)
    c_score = category_score(topic, paper)
    l_score = lexical_overlap_score(topic, paper)
    base_score = round(0.50 * k_score + 0.25 * c_score + 0.25 * l_score, 3)
    reason_parts = []
    if hits:
        reason_parts.append("关键词命中：" + "、".join(hits))
    if c_score > 0:
        reason_parts.append("arXiv 分类重合：" + "、".join(sorted(set(topic.arxiv_categories) & set(paper.get("categories", [])))))
    if not reason_parts:
        reason_parts.append("文本语义与方向描述存在弱相关，需要人工复核。")
    return {
        "topic_id": topic.id,
        "topic_name": topic.name,
        "score": base_score,
        "level": match_level(base_score),
        "reason": "；".join(reason_parts),
        "keyword_hits": hits,
    }


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def is_placeholder_conference_summary(paper: dict[str, Any]) -> bool:
    if paper.get("source_type") != "conference" or paper.get("abstract_source"):
        return False
    summary = normalize_space(str(paper.get("summary") or ""))
    return summary.startswith("DBLP 题录")


def has_meaningful_summary(paper: dict[str, Any], min_chars: int = 80) -> bool:
    if is_placeholder_conference_summary(paper):
        return False
    summary = normalize_space(str(paper.get("summary") or ""))
    return len(summary) >= min_chars


def is_relevant_enough(paper: dict[str, Any], best_match: dict[str, Any]) -> bool:
    if best_match.get("keyword_hits"):
        return True

    score = float(best_match.get("score") or 0.0)
    if paper.get("source_type") == "conference":
        return score >= env_float("MIN_CONFERENCE_SCORE", 0.18)
    if not has_meaningful_summary(paper):
        return score >= env_float("MIN_TITLE_ONLY_SCORE", 0.18)
    return score >= env_float("MIN_PAPER_SCORE", 0.08)


def enrich_conference_papers_from_arxiv(papers: list[dict[str, Any]]) -> dict[str, Any]:
    max_enrichments = max(
        0,
        int(os.getenv("MAX_CONFERENCE_ABSTRACT_ENRICHMENTS", os.getenv("MAX_CONFERENCE_ARXIV_ENRICHMENTS", "50"))),
    )
    delay_seconds = float(os.getenv("CONFERENCE_ABSTRACT_DELAY_SECONDS", os.getenv("CONFERENCE_ARXIV_DELAY_SECONDS", "3")))
    search_results = max(
        1,
        int(os.getenv("CONFERENCE_ABSTRACT_SEARCH_RESULTS", os.getenv("CONFERENCE_ARXIV_SEARCH_RESULTS", "5"))),
    )
    abstract_sources = conference_abstract_sources()
    arxiv_enrichment_enabled = "arxiv" in {source.strip().lower() for source in abstract_sources}
    stats: dict[str, Any] = {
        "conference_abstract_enrichment_attempted": 0,
        "conference_abstract_enrichment_succeeded": 0,
        "conference_abstract_enrichment_skipped": 0,
        "conference_abstract_enrichment_sources": abstract_sources,
        "conference_arxiv_enrichment_attempted": 0,
        "conference_arxiv_enrichment_succeeded": 0,
        "conference_arxiv_enrichment_skipped": 0,
        "conference_arxiv_enrichment_last_error": "",
    }
    if max_enrichments <= 0:
        return stats

    attempts = 0
    for paper in papers:
        if paper.get("source_type") != "conference" or has_meaningful_summary(paper):
            continue
        if attempts >= max_enrichments:
            stats["conference_abstract_enrichment_skipped"] += 1
            stats["conference_arxiv_enrichment_skipped"] += 1
            continue
        title = str(paper.get("title") or "")
        if not title:
            continue

        attempts += 1
        stats["conference_abstract_enrichment_attempted"] += 1
        if arxiv_enrichment_enabled:
            stats["conference_arxiv_enrichment_attempted"] += 1
        candidate = find_conference_abstract_by_title(title, max_results=search_results)
        if candidate and enrich_conference_paper_from_candidate(paper, candidate):
            stats["conference_abstract_enrichment_succeeded"] += 1
            if str(paper.get("abstract_source") or "").lower() == "arxiv":
                stats["conference_arxiv_enrichment_succeeded"] += 1
            print(f"Enriched conference paper from {paper.get('abstract_source')}: {paper.get('title')}", flush=True)

        if attempts < max_enrichments and delay_seconds > 0:
            time.sleep(delay_seconds)
    return stats



# =========================================================
# Model figure extraction
# =========================================================

FIGURE_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?)\s*(\d+[a-z]?)\s*[:.\-–—]?\s*(.*)$",
    flags=re.I,
)

MODEL_FIGURE_POSITIVE_WEIGHTS = {
    "overall architecture": 3.8,
    "model architecture": 3.5,
    "network architecture": 3.3,
    "system architecture": 3.3,
    "overall framework": 3.6,
    "proposed framework": 3.4,
    "framework overview": 3.2,
    "overview of the framework": 3.2,
    "overview of our framework": 3.2,
    "overall pipeline": 3.2,
    "method pipeline": 3.0,
    "proposed method": 2.8,
    "method overview": 3.0,
    "model overview": 3.0,
    "system overview": 2.8,
    "workflow": 2.4,
    "pipeline": 2.2,
    "framework": 2.0,
    "architecture": 2.0,
    "our model": 1.8,
    "proposed model": 2.4,
}

MODEL_FIGURE_NEGATIVE_WEIGHTS = {
    "ablation": 3.0,
    "comparison": 2.5,
    "performance": 2.2,
    "results": 2.2,
    "accuracy": 2.0,
    "distribution": 2.0,
    "visualization": 1.8,
    "qualitative": 1.8,
    "quantitative": 1.8,
    "confusion matrix": 2.5,
    "attention map": 2.0,
    "t-sne": 2.5,
    "tsne": 2.5,
    "examples": 1.2,
    "case study": 1.6,
}


def model_figure_enabled() -> bool:
    return env_flag("ENABLE_MODEL_FIGURE", True)


def model_figure_output_dir() -> Path:
    return Path(os.getenv("MODEL_FIGURE_DIR", str(DEFAULT_FIGURE_DIR)))


def figure_filename_for_paper(paper: dict[str, Any], figure_number: str) -> str:
    identity = str(paper.get("id") or paper.get("paper_url") or paper.get("title") or "paper")
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    readable = slugify(identity)[:56] or "paper"
    number = slugify(str(figure_number)) or "figure"
    return f"{readable}-{digest}-{number}.png"


def download_pdf_limited(url: str) -> bytes:
    if not url or not re.match(r"^https?://", url, flags=re.I):
        raise ValueError("No downloadable HTTP(S) PDF URL is available.")

    timeout = env_float("PDF_TIMEOUT_SECONDS", 45.0)
    max_bytes = max(1, env_int("PDF_MAX_BYTES", 30 * 1024 * 1024))

    headers = {
        "User-Agent": "paper-daily-collector/1.0 (+https://github.com/Coder-jzq/paper-daily)",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
    }
    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ValueError(
                        f"PDF is too large ({content_length} bytes > {max_bytes} bytes)."
                    )
            except ValueError as exc:
                if "PDF is too large" in str(exc):
                    raise

        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = response.read(min(1024 * 1024, max_bytes - received + 1))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > max_bytes:
                raise ValueError(f"PDF exceeds PDF_MAX_BYTES={max_bytes}.")

    data = b"".join(chunks)
    content_type = str(response.headers.get("Content-Type") or "").lower()

    if not data.startswith(b"%PDF") and "pdf" not in content_type:
        raise ValueError("The configured PDF URL did not return a PDF document.")

    if not data.startswith(b"%PDF"):
        # Some servers prepend a tiny wrapper before the PDF header.
        header_index = data.find(b"%PDF")
        if 0 <= header_index <= 1024:
            data = data[header_index:]
        else:
            raise ValueError("Downloaded content does not contain a valid PDF header.")

    return data


def figure_caption_score(caption: str, figure_number: int | None = None) -> float:
    text = normalize_space(caption).lower()
    if not text:
        return 0.0

    raw_score = 0.0
    for phrase, weight in MODEL_FIGURE_POSITIVE_WEIGHTS.items():
        if phrase in text:
            raw_score += weight

    for phrase, weight in MODEL_FIGURE_NEGATIVE_WEIGHTS.items():
        if phrase in text:
            raw_score -= weight

    # Earlier figures are slightly more likely to be the overall architecture.
    if figure_number is not None and 1 <= figure_number <= 4:
        raw_score += max(0.0, 0.45 - 0.08 * (figure_number - 1))

    if 20 <= len(text) <= 800:
        raw_score += 0.15

    # Map the heuristic score to 0..1 for easier configuration.
    return round(max(0.0, min(1.0, raw_score / 6.0)), 3)


def figure_confidence(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.52:
        return "medium"
    return "low"


def extract_figure_caption_candidates(document: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    max_pages = max(1, env_int("MODEL_FIGURE_MAX_PAGES", 12))
    for page_index in range(min(len(document), max_pages)):
        page = document[page_index]
        for block in page.get_text("blocks"):
            if len(block) < 5:
                continue

            raw_text = normalize_space(str(block[4] or ""))
            if not raw_text:
                continue

            match = FIGURE_CAPTION_RE.match(raw_text)
            if not match:
                continue

            number_text = match.group(1)
            numeric_match = re.match(r"\d+", number_text)
            number_int = int(numeric_match.group(0)) if numeric_match else None
            score = figure_caption_score(raw_text, number_int)

            candidates.append(
                {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "figure_number": f"Figure {number_text}",
                    "caption": raw_text,
                    "bbox": tuple(float(value) for value in block[:4]),
                    "score": score,
                }
            )

    candidates.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            -int(item.get("page_index") or 0),
        ),
        reverse=True,
    )
    return candidates


def horizontal_overlap_ratio(left: Any, right: Any) -> float:
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    denominator = max(1.0, min(left.width, right.width))
    return overlap / denominator


def infer_figure_crop_rect(page: Any, caption_bbox: tuple[float, float, float, float]) -> Any:
    import fitz

    page_rect = page.rect
    caption_rect = fitz.Rect(*caption_bbox)
    page_width = page_rect.width
    page_height = page_rect.height

    full_width_caption = (
        caption_rect.width >= page_width * 0.58
        or (
            caption_rect.x0 <= page_width * 0.18
            and caption_rect.x1 >= page_width * 0.52
        )
        or (
            caption_rect.x0 <= page_width * 0.16
            and caption_rect.x1 >= page_width * 0.84
        )
    )

    horizontal_margin = max(8.0, page_width * 0.025)

    if full_width_caption:
        crop_x0 = page_rect.x0 + horizontal_margin
        crop_x1 = page_rect.x1 - horizontal_margin
    else:
        center = (caption_rect.x0 + caption_rect.x1) / 2.0
        column_gap = max(8.0, page_width * 0.015)

        if center <= page_width / 2.0:
            crop_x0 = page_rect.x0 + horizontal_margin
            crop_x1 = page_rect.x0 + page_width / 2.0 - column_gap
        else:
            crop_x0 = page_rect.x0 + page_width / 2.0 + column_gap
            crop_x1 = page_rect.x1 - horizontal_margin

    crop_x0 = max(page_rect.x0, crop_x0)
    crop_x1 = min(page_rect.x1, crop_x1)

    target_horizontal = fitz.Rect(
        crop_x0,
        page_rect.y0,
        crop_x1,
        caption_rect.y0,
    )

    max_lookback = min(page_height * 0.50, env_float("MODEL_FIGURE_MAX_HEIGHT_POINTS", 390.0))
    crop_y0 = max(page_rect.y0 + 18.0, caption_rect.y0 - max_lookback)

    # Find the nearest paragraph-like block above the caption. Long prose blocks
    # are more likely to be body text than labels embedded inside a diagram.
    nearest_body_bottom = None
    for block in page.get_text("blocks"):
        if len(block) < 5:
            continue

        block_rect = fitz.Rect(*block[:4])
        block_text = normalize_space(str(block[4] or ""))

        if block_rect.y1 >= caption_rect.y0 - 4:
            continue
        if block_rect.y1 < crop_y0:
            continue
        if len(block_text) < 110 or len(block_text.split()) < 16:
            continue
        if FIGURE_CAPTION_RE.match(block_text):
            continue
        if horizontal_overlap_ratio(block_rect, target_horizontal) < 0.48:
            continue

        if nearest_body_bottom is None or block_rect.y1 > nearest_body_bottom:
            nearest_body_bottom = block_rect.y1

    if nearest_body_bottom is not None:
        crop_y0 = max(crop_y0, nearest_body_bottom + 7.0)

    crop_y1 = max(crop_y0 + 1.0, caption_rect.y0 - 4.0)

    # If the inferred region is implausibly short, use a conservative window
    # directly above the caption.
    min_height = env_float("MODEL_FIGURE_MIN_HEIGHT_POINTS", 72.0)
    if crop_y1 - crop_y0 < min_height:
        crop_y0 = max(page_rect.y0 + 18.0, caption_rect.y0 - min(250.0, page_height * 0.34))

    return fitz.Rect(crop_x0, crop_y0, crop_x1, crop_y1) & page_rect


def extract_model_figure(paper: dict[str, Any]) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "figure_number": "",
        "page": 0,
        "image": "",
        "caption": analysis_pair("", ""),
        "confidence": "none",
        "score": 0.0,
        "selection_method": "caption_heuristic",
    }

    if not model_figure_enabled():
        unavailable["reason"] = "disabled"
        return unavailable

    pdf_url = normalize_space(str(paper.get("pdf_url") or ""))
    if not pdf_url:
        unavailable["reason"] = "no_pdf_url"
        return unavailable

    try:
        import fitz
    except ImportError:
        unavailable["reason"] = "pymupdf_not_installed"
        return unavailable

    try:
        pdf_bytes = download_pdf_limited(pdf_url)
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        unavailable["reason"] = f"pdf_open_failed: {exc}"
        return unavailable

    try:
        candidates = extract_figure_caption_candidates(document)
        min_score = env_float("MODEL_FIGURE_MIN_SCORE", 0.48)
        candidate = next(
            (item for item in candidates if float(item.get("score") or 0.0) >= min_score),
            None,
        )

        if not candidate:
            unavailable["reason"] = "no_architecture_figure_candidate"
            return unavailable

        page = document[int(candidate["page_index"])]
        clip = infer_figure_crop_rect(page, candidate["bbox"])

        if clip.width < 80 or clip.height < 60:
            unavailable["reason"] = "figure_crop_too_small"
            return unavailable

        output_dir = model_figure_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = figure_filename_for_paper(
            paper,
            str(candidate["figure_number"]),
        )
        output_path = output_dir / filename

        dpi = max(96, env_int("MODEL_FIGURE_DPI", 180))
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        pixmap.save(str(output_path))

        image_rel = f"./data/figures/{filename}"

        return {
            "available": True,
            "figure_number": str(candidate["figure_number"]),
            "page": int(candidate["page_number"]),
            "image": image_rel,
            "caption": analysis_pair(str(candidate["caption"]), ""),
            "confidence": figure_confidence(float(candidate["score"])),
            "score": float(candidate["score"]),
            "selection_method": "caption_heuristic",
            "source_pdf_url": pdf_url,
        }
    except Exception as exc:
        unavailable["reason"] = f"figure_extract_failed: {exc}"
        return unavailable
    finally:
        document.close()


def model_figure_file_exists(model_figure: dict[str, Any]) -> bool:
    image = normalize_space(str(model_figure.get("image") or ""))
    if not image:
        return False

    prefix = "./data/figures/"
    if image.startswith(prefix):
        return (model_figure_output_dir() / image[len(prefix):]).exists()

    return False


def enrich_model_figures(papers: list[dict[str, Any]]) -> dict[str, Any]:
    stats = {
        "model_figure_enabled": model_figure_enabled(),
        "model_figure_attempted": 0,
        "model_figure_succeeded": 0,
        "model_figure_reused": 0,
        "model_figure_skipped": 0,
        "model_figure_dependency_available": True,
    }

    if not model_figure_enabled():
        return stats

    try:
        import fitz  # noqa: F401
    except ImportError:
        stats["model_figure_dependency_available"] = False
        stats["model_figure_skipped"] = len(papers)
        print(
            "Warning: ENABLE_MODEL_FIGURE is enabled but PyMuPDF is not installed; "
            "model figure extraction is skipped.",
            file=sys.stderr,
        )
        return stats

    max_figures = max(0, env_int("MAX_MODEL_FIGURES_PER_RUN", 20))
    delay_seconds = max(0.0, env_float("MODEL_FIGURE_DELAY_SECONDS", 1.0))
    attempts = 0

    for paper in papers:
        existing = paper.get("model_figure")
        if isinstance(existing, dict) and existing.get("available") and model_figure_file_exists(existing):
            stats["model_figure_reused"] += 1
            continue

        if attempts >= max_figures:
            stats["model_figure_skipped"] += 1
            continue

        if not normalize_space(str(paper.get("pdf_url") or "")):
            paper["model_figure"] = {
                "available": False,
                "reason": "no_pdf_url",
            }
            stats["model_figure_skipped"] += 1
            continue

        attempts += 1
        stats["model_figure_attempted"] += 1

        figure = extract_model_figure(paper)
        paper["model_figure"] = figure

        if figure.get("available"):
            stats["model_figure_succeeded"] += 1
            print(
                f"Extracted model figure for {paper.get('id')}: "
                f"{figure.get('figure_number')} page={figure.get('page')} "
                f"score={figure.get('score')}",
                flush=True,
            )
        else:
            stats["model_figure_skipped"] += 1
            print(
                f"Model figure not found for {paper.get('id')}: {figure.get('reason', 'unknown')}",
                flush=True,
            )

        if attempts < max_figures and delay_seconds > 0:
            time.sleep(delay_seconds)

    return stats


def prune_unreferenced_model_figures(papers: list[dict[str, Any]]) -> int:
    output_dir = model_figure_output_dir()
    if not output_dir.exists():
        return 0

    keep: set[str] = set()
    prefix = "./data/figures/"

    for paper in papers:
        figure = paper.get("model_figure")
        if not isinstance(figure, dict) or not figure.get("available"):
            continue
        image = normalize_space(str(figure.get("image") or ""))
        if image.startswith(prefix):
            keep.add(Path(image[len(prefix):]).name)

    removed = 0
    for path in output_dir.iterdir():
        if not path.is_file():
            continue
        if path.name in keep:
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue

        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            print(f"Warning: cannot remove stale model figure {path}: {exc}", file=sys.stderr)

    return removed


def should_summarize_paper_with_llm(paper: dict[str, Any]) -> bool:
    has_summary = has_meaningful_summary(paper)
    if paper.get("source_type") == "conference" and not has_summary:
        return env_flag("LLM_SUMMARIZE_CONFERENCE", False) and env_flag("LLM_SUMMARIZE_TITLE_ONLY", False)
    if paper.get("source_type") == "conference" and not env_flag("LLM_SUMMARIZE_CONFERENCE", True):
        return False
    if not has_summary and not env_flag("LLM_SUMMARIZE_TITLE_ONLY", False):
        return False
    return True


BILINGUAL_ANALYSIS_FIELDS = (
    "task_intro",
    "problem",
    "method",
    "innovation",
    "evidence",
    "limitations",
    "why_relevant",
)


def analysis_pair(original: str = "", zh: str = "") -> dict[str, str]:
    return {
        "original": normalize_space(str(original or "")),
        "zh": normalize_space(str(zh or "")),
    }


def normalize_analysis_pair(value: Any) -> dict[str, str] | None:
    if isinstance(value, dict):
        original = (
            value.get("original")
            or value.get("en")
            or value.get("english")
            or value.get("source")
            or ""
        )
        zh = (
            value.get("zh")
            or value.get("cn")
            or value.get("chinese")
            or value.get("translation")
            or ""
        )
        pair = analysis_pair(str(original or ""), str(zh or ""))
        if pair["original"] or pair["zh"]:
            return pair
        return None

    if isinstance(value, str) and value.strip():
        # Compatibility with an older model response that returned a Chinese string.
        return analysis_pair("", value)

    return None


def normalize_analysis_pair_list(
    value: Any,
    fallback: list[dict[str, str]] | None = None,
    max_items: int = 4,
) -> list[dict[str, str]]:
    raw_items = ensure_list(value)
    pairs: list[dict[str, str]] = []
    for raw_item in raw_items:
        pair = normalize_analysis_pair(raw_item)
        if not pair:
            continue
        pairs.append(pair)
        if len(pairs) >= max_items:
            break

    if pairs:
        return pairs

    return copy.deepcopy(fallback or [])


def analysis_to_legacy_summary(analysis: dict[str, Any]) -> dict[str, str]:
    """
    Convert the new bilingual sentence-pair schema to the historical
    chinese_summary schema so the old web UI remains usable during migration.
    """
    legacy: dict[str, str] = {}
    for field in BILINGUAL_ANALYSIS_FIELDS:
        values = normalize_analysis_pair_list(analysis.get(field), max_items=8)
        chinese = [pair["zh"] for pair in values if pair.get("zh")]
        if not chinese:
            chinese = [pair["original"] for pair in values if pair.get("original")]
        legacy[field] = " ".join(chinese).strip()
    return legacy


def fallback_analysis(paper: dict[str, Any], best_match: dict[str, Any]) -> dict[str, Any]:
    title = normalize_space(str(paper.get("title") or ""))
    reason_zh = normalize_space(str(best_match.get("reason") or "与配置方向存在文本匹配。"))

    if paper.get("source_type") == "conference" and not has_meaningful_summary(paper):
        return {
            "schema_version": 3,
            "title": analysis_pair(title, ""),
            "task_intro": [
                analysis_pair(
                    "The exact research task cannot be defined reliably because the conference index does not provide a usable abstract.",
                    "由于会议索引没有提供可用摘要，目前无法可靠定义这篇论文所研究的具体任务。",
                )
            ],
            "problem": [
                analysis_pair(
                    "The conference index does not provide enough evidence to reconstruct the concrete research problem addressed by this paper.",
                    "会议索引提供的信息不足以可靠还原这篇论文所解决的具体研究问题。",
                )
            ],
            "method": [
                analysis_pair(
                    "The method architecture, core modules, information flow, and training procedure require the abstract or full paper.",
                    "方法的整体架构、核心模块、信息流以及训练过程需要结合摘要或论文全文才能判断。",
                )
            ],
            "innovation": [
                analysis_pair(
                    "The paper's technical innovations cannot be determined reliably from bibliographic information alone.",
                    "仅凭题录信息无法可靠判断论文的技术创新点。",
                )
            ],
            "evidence": [
                analysis_pair(
                    "Only bibliographic information from the conference index is currently available.",
                    "当前仅有会议索引提供的题录信息可供核验。",
                )
            ],
            "limitations": [
                analysis_pair(
                    "Automatic technical analysis is limited until a reliable abstract is found from arXiv, OpenAlex, Crossref, or another trusted source.",
                    "在 arXiv、OpenAlex、Crossref 或其他可信来源找到可靠摘要之前，自动技术分析能力会受到限制。",
                )
            ],
            "why_relevant": [
                analysis_pair(
                    "The paper has a textual match with one of the configured research interests.",
                    reason_zh,
                )
            ],
        }

    if not has_meaningful_summary(paper):
        return {
            "schema_version": 3,
            "title": analysis_pair(title, ""),
            "task_intro": [
                analysis_pair(
                    "The source does not provide enough abstract information to define the paper's input, output, and research objective reliably.",
                    "来源没有提供足够的摘要信息，因此无法可靠定义论文任务的输入、输出和研究目标。",
                )
            ],
            "problem": [
                analysis_pair(
                    "The source does not provide enough abstract information for a reliable problem analysis.",
                    "来源没有提供足够摘要信息，因此无法可靠分析论文所解决的问题。",
                )
            ],
            "method": [
                analysis_pair(
                    "The method architecture and technical pipeline should be inspected from the abstract or full paper.",
                    "方法架构与技术流程需要结合摘要或论文全文进一步查看。",
                )
            ],
            "innovation": [
                analysis_pair(
                    "The innovations cannot be extracted reliably from the available metadata.",
                    "现有元数据不足以可靠提取论文创新点。",
                )
            ],
            "evidence": [
                analysis_pair(
                    "More evidence is required from the abstract or full paper.",
                    "需要从摘要或论文全文中获取更多证据。",
                )
            ],
            "limitations": [
                analysis_pair(
                    "Missing abstract information reduces the reliability of task definition, technical analysis, and bilingual summarization.",
                    "缺少摘要信息会降低任务定义、技术分析和双语总结的可靠性。",
                )
            ],
            "why_relevant": [
                analysis_pair(
                    "The paper has a textual match with one of the configured research interests.",
                    reason_zh,
                )
            ],
        }

    return {
        "schema_version": 3,
        "title": analysis_pair(title, ""),
        "task_intro": [
            analysis_pair(
                "The source abstract is available, but the configured LLM analysis is unavailable, so a reliable detailed task definition is not generated automatically.",
                "来源摘要可用，但当前无法使用已配置的 LLM，因此不会自动生成详细任务定义。",
            )
        ],
        "problem": [
            analysis_pair(
                "The LLM analysis is unavailable, so only a basic metadata-based assessment is shown.",
                "当前无法使用 LLM 分析，因此仅展示基于元数据的基础判断。",
            )
        ],
        "method": [
            analysis_pair(
                "Please refer to the source abstract or paper page for the detailed architecture, modules, information flow, and training procedure.",
                "请参考来源摘要或论文页面查看详细的架构、模块、信息流和训练过程。",
            )
        ],
        "innovation": [
            analysis_pair(
                "A precise innovation analysis requires the configured LLM.",
                "精确的创新点分析需要启用已配置的 LLM。",
            )
        ],
        "evidence": [
            analysis_pair(
                "The source abstract can be checked against the original paper.",
                "来源摘要可与论文原文进行核验。",
            )
        ],
        "limitations": [
            analysis_pair(
                "The fallback mode does not perform deep technical decomposition or sentence-aligned bilingual analysis.",
                "基础回退模式不会进行深度技术拆解或逐句双语分析。",
            )
        ],
        "why_relevant": [
            analysis_pair(
                "The paper has a textual match with one of the configured research interests.",
                reason_zh,
            )
        ],
    }


def fallback_summary(paper: dict[str, Any], best_match: dict[str, Any]) -> dict[str, str]:
    """Legacy Chinese-only summary retained for backward compatibility."""
    return analysis_to_legacy_summary(fallback_analysis(paper, best_match))


def llm_enabled() -> bool:
    return bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))


def llm_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "paper-daily-collector/1.0",
    }


def parse_llm_json_text(content: str) -> dict[str, Any]:
    """Parse JSON returned by the model, tolerating accidental Markdown fences."""
    content = content.strip()
    fenced = re.match(r"^```(?:json)?\\s*(.*?)\\s*```$", content, flags=re.S | re.I)
    if fenced:
        content = fenced.group(1).strip()
    return json.loads(content)


def responses_output_text(data: dict[str, Any]) -> str:
    """
    Extract text from an OpenAI Responses API compatible response.

    Supports:
      1) top-level output_text
      2) output[*].content[*].text
    """
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for item in ensure_list(data.get("output")):
        if not isinstance(item, dict):
            continue
        for content in ensure_list(item.get("content")):
            if not isinstance(content, dict):
                continue
            text_value = content.get("text")
            if text_value:
                chunks.append(str(text_value))

    if chunks:
        return "\\n".join(chunks).strip()

    raise ValueError("Responses API returned no output text")


def call_responses_api(prompt: str, api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """
    Call an OpenAI Responses API compatible endpoint.

    Examples:
      LLM_BASE_URL=https://aihub.top
        -> https://aihub.top/responses

      LLM_BASE_URL=https://aihub.top/v1
        -> https://aihub.top/v1/responses
    """
    endpoint = base_url.rstrip("/") + "/responses"

    payload: dict[str, Any] = {
        "model": model,
        "instructions": "你是严谨的论文技术分析助手。只输出合法 JSON，不要输出 Markdown。",
        "input": prompt,
        "store": False,
    }

    max_output_tokens = env_int("LLM_MAX_OUTPUT_TOKENS", 0)
    if max_output_tokens > 0:
        payload["max_output_tokens"] = max_output_tokens

    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

    print(
        f"LLM request: mode=responses endpoint={endpoint} model={model}",
        flush=True,
    )

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=llm_headers(api_key),
        method="POST",
    )

    timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return parse_llm_json_text(responses_output_text(data))


def call_chat_completions_api(prompt: str, api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """Fallback for traditional OpenAI-compatible Chat Completions endpoints."""
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "你是严谨的论文技术分析助手。只输出合法 JSON，不要输出 Markdown。",
            },
            {"role": "user", "content": prompt},
        ],
    }

    print(
        f"LLM request: mode=chat_completions endpoint={endpoint} model={model}",
        flush=True,
    )

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=llm_headers(api_key),
        method="POST",
    )

    timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    content = data["choices"][0]["message"]["content"]
    return parse_llm_json_text(str(content))


def call_openai_compatible(prompt: str) -> dict[str, Any]:
    """
    Call the configured LLM endpoint.

    LLM_API_MODE:
      responses        -> force /responses
      chat_completions -> force /chat/completions
      auto             -> try /responses first, fallback to chat/completions on 404/405

    For aihub.top Codex-style configuration, use:
      LLM_BASE_URL=https://aihub.top
      LLM_MODEL=gpt-5.6
      LLM_API_MODE=responses
    """
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or ""
    )

    base_url = os.getenv("LLM_BASE_URL", "").strip()
    if not base_url:
        base_url = (
            "https://api.deepseek.com/v1"
            if os.getenv("DEEPSEEK_API_KEY")
            else "https://api.openai.com/v1"
        )

    model = os.getenv(
        "LLM_MODEL",
        "deepseek-chat" if os.getenv("DEEPSEEK_API_KEY") else "gpt-4o-mini",
    ).strip()

    mode = os.getenv("LLM_API_MODE", "auto").strip().lower()

    if mode in {"responses", "response"}:
        return call_responses_api(prompt, api_key, base_url, model)

    if mode in {
        "chat",
        "chat_completions",
        "chat-completions",
        "chatcompletions",
    }:
        return call_chat_completions_api(prompt, api_key, base_url, model)

    if mode != "auto":
        print(
            f"Warning: unknown LLM_API_MODE={mode!r}; using auto mode.",
            file=sys.stderr,
        )

    try:
        return call_responses_api(prompt, api_key, base_url, model)
    except urllib.error.HTTPError as exc:
        # Only fall back when the Responses endpoint itself is unavailable.
        # Authentication, model, quota and payload errors should remain visible.
        if exc.code not in {404, 405}:
            raise

        print(
            f"Responses API unavailable (HTTP {exc.code}); "
            "falling back to chat/completions.",
            flush=True,
        )
        return call_chat_completions_api(prompt, api_key, base_url, model)


def build_llm_prompt(topic: Topic, paper: dict[str, Any], base_match: dict[str, Any]) -> str:
    abstract_label = "abstract / bibliographic information" if paper.get("source_type") == "conference" else "abstract"
    paper_title = normalize_space(str(paper.get("title") or ""))

    return f"""
You are analyzing a research paper for a serious personal paper-reading dashboard.

Produce a detailed but disciplined bilingual technical reading note in sentence-aligned English-Chinese pairs.
The English sentence is the primary technical statement; the Chinese sentence must be its faithful, natural translation.

CORE PRINCIPLE:
Every technical claim MUST be supported by the supplied title, abstract/bibliographic information, categories, or research-interest context.
Do not invent any experiment, dataset, metric, model component, loss function, training strategy, result, or conclusion.

SECTION REQUIREMENTS:

1. title
   - title.original MUST reproduce the supplied paper title exactly.
   - title.zh is a faithful Chinese translation.

2. task_intro
   Explain the research TASK itself before discussing this paper's contribution.
   Prefer 2-3 sentence pairs:
   - What research direction/task is being studied?
   - What is the typical input or observed information?
   - What output, prediction, generation, retrieval, reasoning result, or optimization objective is expected?
   - If the abstract does not make input/output explicit, describe only what can be supported and say what remains unspecified.
   - This section should help a reader unfamiliar with the area understand what the task means.

3. problem
   Make the problem analysis much clearer than a short abstract paraphrase.
   Prefer 3-4 sentence pairs that form a logical chain:
   - What do existing approaches or current practice do?
   - What limitation, bottleneck, mismatch, or unresolved challenge is identified?
   - Why is that limitation technically important?
   - What concrete problem does this paper therefore aim to solve?
   Do not invent a criticism if the abstract does not state one. Use cautious wording such as
   "The abstract motivates..." or "The abstract does not specify..." where appropriate.

4. method
   This should be the most detailed section.
   Prefer 4-7 sentence pairs and explain the technical pipeline in reading order:
   - overall framework or main idea;
   - input representation / encoder / backbone, if stated;
   - key modules or stages;
   - how information flows or interacts between modules;
   - training objective / optimization / supervision, if stated;
   - inference or output stage, if stated.
   Each sentence pair should describe ONE technical step.
   Preserve exact method names, module names, acronyms, datasets, and task terminology appearing in the abstract.
   If details are absent, explicitly say they are not reported in the abstract instead of filling them in.

5. innovation
   Prefer 2-4 sentence pairs.
   Each pair should state ONE distinct innovation or contribution.
   Explain what is new relative to the limitation/problem described above, not merely repeat the method.
   Do not call something "first", "novel", "state-of-the-art", or "significant" unless supported by the supplied source.

6. evidence
   Prefer 1-3 sentence pairs.
   Report only evidence explicitly supported by the source:
   - experiments,
   - datasets,
   - benchmarks,
   - quantitative improvements,
   - theoretical analysis,
   - human evaluation,
   - ablations,
   - or other validation.
   Never invent numbers.

7. limitations
   Prefer 1-2 sentence pairs.
   Distinguish explicit limitations from missing information.
   When limitations are not stated, use cautious statements such as:
   "The abstract does not report performance under ..."
   Do not manufacture weaknesses.

8. why_relevant
   Prefer 1-2 sentence pairs.
   Explain the connection to the configured research interest.
   Relevance should be strict and technical rather than generic.

PAIR FORMAT:
- Every section above except title MUST be an array.
- Every array element MUST contain exactly:
  {{"original": "One complete English sentence.", "zh": "对应的一句中文。"}}
- Keep English and Chinese one-to-one and in the same order.
- Do not combine several unrelated claims into one pair.

RELEVANCE:
- If the paper is only broadly related, lower match_level to medium or low.
- match_score_adjustment should normally be modest.

OUTPUT:
Return ONLY valid JSON. No Markdown. No explanation outside JSON.

Research interest:
Name: {topic.name}
Description: {topic.description}
Keywords: {", ".join(topic.keywords)}

Paper:
Title: {paper_title}
Authors: {", ".join(paper.get("authors", [])[:8])}
Categories: {", ".join(paper.get("categories", []))}
{abstract_label}: {paper.get("summary", "")}

Base relevance:
Score: {base_match.get("score")}
Level: {base_match.get("level")}
Reason: {base_match.get("reason")}

Return JSON with EXACTLY this structure:
{{
  "title": {{
    "original": {json.dumps(paper_title, ensure_ascii=False)},
    "zh": "中文标题"
  }},
  "task_intro": [
    {{"original": "One English task-definition sentence.", "zh": "对应的一句中文。"}}
  ],
  "problem": [
    {{"original": "One English problem-analysis sentence.", "zh": "对应的一句中文。"}}
  ],
  "method": [
    {{"original": "One English technical-method sentence.", "zh": "对应的一句中文。"}}
  ],
  "innovation": [
    {{"original": "One English innovation sentence.", "zh": "对应的一句中文。"}}
  ],
  "evidence": [
    {{"original": "One English evidence sentence.", "zh": "对应的一句中文。"}}
  ],
  "limitations": [
    {{"original": "One English limitation sentence.", "zh": "对应的一句中文。"}}
  ],
  "why_relevant": [
    {{"original": "One English relevance sentence.", "zh": "对应的一句中文。"}}
  ],
  "match_score_adjustment": 0.0,
  "match_level": "high|medium|low"
}}
""".strip()


def normalize_llm_analysis(
    data: dict[str, Any],
    paper: dict[str, Any],
    best_match: dict[str, Any],
) -> dict[str, Any]:
    fallback = fallback_analysis(paper, best_match)
    paper_title = normalize_space(str(paper.get("title") or ""))

    title_value = data.get("title")
    title_zh = ""
    if isinstance(title_value, dict):
        title_zh = normalize_space(
            str(
                title_value.get("zh")
                or title_value.get("cn")
                or title_value.get("chinese")
                or title_value.get("translation")
                or ""
            )
        )
    elif isinstance(title_value, str):
        # Tolerate a model that returns only a Chinese title string.
        title_zh = normalize_space(title_value)

    analysis: dict[str, Any] = {
        "schema_version": 3,
        # Always trust the source title rather than a model-regenerated English title.
        "title": analysis_pair(paper_title, title_zh),
    }

    max_items_by_field = {
        "task_intro": 3,
        "problem": 4,
        "method": 7,
        "innovation": 4,
        "evidence": 3,
        "limitations": 2,
        "why_relevant": 2,
    }

    for field in BILINGUAL_ANALYSIS_FIELDS:
        analysis[field] = normalize_analysis_pair_list(
            data.get(field),
            fallback=fallback.get(field, []),
            max_items=max_items_by_field[field],
        )

    return analysis


def summarize_with_llm(
    topic: Topic,
    paper: dict[str, Any],
    base_match: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not llm_enabled():
        return fallback_analysis(paper, base_match), base_match

    prompt = build_llm_prompt(topic, paper, base_match)
    try:
        data = call_openai_compatible(prompt)
    except Exception as exc:
        print(f"Warning: LLM summary failed for {paper.get('id')}: {exc}", file=sys.stderr)
        return fallback_analysis(paper, base_match), base_match

    analysis = normalize_llm_analysis(data, paper, base_match)

    try:
        adjustment = float(data.get("match_score_adjustment", 0.0) or 0.0)
    except (TypeError, ValueError):
        adjustment = 0.0

    adjusted_score = max(0.0, min(1.0, float(base_match["score"]) + adjustment))
    adjusted_level = str(data.get("match_level") or match_level(adjusted_score)).lower()
    if adjusted_level not in {"high", "medium", "low"}:
        adjusted_level = match_level(adjusted_score)

    adjusted_match = dict(base_match)
    adjusted_match["score"] = round(adjusted_score, 3)
    adjusted_match["level"] = adjusted_level

    legacy = analysis_to_legacy_summary(analysis)
    adjusted_match["llm_reason"] = legacy.get("why_relevant", "")
    adjusted_match["analysis_schema_version"] = 3

    return analysis, adjusted_match


def summarize_one(args: tuple[Topic, dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    topic, paper = args
    paper_id = str(paper.get("id", ""))
    analysis, adjusted_match = summarize_with_llm(topic, paper, paper["best_match"])
    return paper_id, analysis, adjusted_match


def dedupe_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for paper in papers:
        key = paper.get("id") or paper.get("paper_url")
        if key in seen:
            continue
        seen.add(key)
        unique.append(paper)
    return unique


def paper_key(paper: dict[str, Any]) -> str:
    return str(paper.get("id") or paper.get("paper_url") or "")


def best_match_level(paper: dict[str, Any]) -> str:
    return str((paper.get("best_match") or {}).get("level") or "low").lower()


def conference_identity(paper: dict[str, Any]) -> tuple[str, int] | None:
    conference = paper.get("conference")
    if not isinstance(conference, dict):
        return None
    conference_id = str(conference.get("id") or "")
    try:
        year = int(conference.get("year"))
    except (TypeError, ValueError):
        return None
    if not conference_id or year <= 0:
        return None
    return conference_id, year


def cached_conference_years(existing_payload: dict[str, Any]) -> dict[str, set[int]]:
    cached: dict[str, set[int]] = {}
    papers = existing_payload.get("papers", []) if isinstance(existing_payload, dict) else []
    for paper in papers:
        if not isinstance(paper, dict) or paper.get("source_type") != "conference":
            continue
        identity = conference_identity(paper)
        if not identity:
            continue
        conference_id, year = identity
        cached.setdefault(conference_id, set()).add(year)
    return cached


def active_conference_years(sources: list[ConferenceSource]) -> dict[str, set[int]]:
    return {source.id: set(source.years) for source in sources}


def uncached_conference_years(source: ConferenceSource, cached_years_by_source: dict[str, set[int]]) -> list[int]:
    cached_years = cached_years_by_source.get(source.id, set())
    return [year for year in source.years if year not in cached_years]


def should_retain_conference_paper(
    paper: dict[str, Any],
    active_years_by_source: dict[str, set[int]] | None,
) -> bool:
    if paper.get("source_type") != "conference" or active_years_by_source is None:
        return False
    identity = conference_identity(paper)
    if not identity:
        return False
    conference_id, year = identity
    return year in active_years_by_source.get(conference_id, set())


def split_conference_payload(existing_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    daily_payload = copy.deepcopy(existing_payload) if isinstance(existing_payload, dict) else {}
    conference_payload = copy.deepcopy(existing_payload) if isinstance(existing_payload, dict) else {}
    papers = existing_payload.get("papers", []) if isinstance(existing_payload, dict) else []
    daily_payload["papers"] = [
        paper for paper in papers if isinstance(paper, dict) and paper.get("source_type") != "conference"
    ]
    conference_payload["papers"] = [
        paper for paper in papers if isinstance(paper, dict) and paper.get("source_type") == "conference"
    ]
    return daily_payload, conference_payload


def load_existing_payload(output_path: Path) -> dict[str, Any]:
    if not output_path.exists():
        return {}
    try:
        return load_json(output_path)
    except Exception as exc:
        print(f"Warning: cannot read existing paper data, starting fresh: {exc}", file=sys.stderr)
        return {}


def merge_with_retained_papers(
    current_papers: list[dict[str, Any]],
    existing_payload: dict[str, Any],
    now: dt.datetime,
    recent_history_days: int,
    active_conference_years_by_source: dict[str, set[int]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    existing_papers = existing_payload.get("papers", []) if isinstance(existing_payload, dict) else []
    existing_generated_at = str(existing_payload.get("generated_at_iso") or existing_payload.get("generated_at") or now.isoformat())
    retained_by_key: dict[str, dict[str, Any]] = {}
    dropped_low = 0
    retained_recent = 0
    for paper in existing_papers:
        if not isinstance(paper, dict):
            continue
        key = paper_key(paper)
        if not key:
            continue
        seen_at = parse_datetime(str(paper.get("first_seen_at") or paper.get("last_seen_at") or existing_generated_at))
        is_recent = bool(
            recent_history_days > 0
            and seen_at
            and (now.date() - seen_at.date()).days <= recent_history_days
        )
        is_active_conference = (
            should_retain_conference_paper(paper, active_conference_years_by_source)
            and is_relevant_enough(paper, paper.get("best_match") or {})
        )
        if paper.get("source_type") == "conference" and active_conference_years_by_source is not None and not is_active_conference:
            dropped_low += 1
            continue
        if best_match_level(paper) in RETAINED_MATCH_LEVELS or is_recent or is_active_conference:
            retained_by_key[key] = paper
            if is_recent and best_match_level(paper) not in RETAINED_MATCH_LEVELS:
                retained_recent += 1
        else:
            dropped_low += 1

    merged = []
    seen = set()
    now_iso = now.isoformat()
    for paper in current_papers:
        key = paper_key(paper)
        previous = retained_by_key.get(key)
        if previous:
            paper.setdefault("first_seen_at", previous.get("first_seen_at") or existing_generated_at)
        else:
            paper.setdefault("first_seen_at", now_iso)
        paper["last_seen_at"] = now_iso
        paper["retained_from_previous_run"] = False
        merged.append(paper)
        if key:
            seen.add(key)

    retained_count = 0
    for key, paper in retained_by_key.items():
        if key in seen:
            continue
        retained = dict(paper)
        retained.setdefault("first_seen_at", existing_generated_at)
        retained.setdefault("last_seen_at", existing_generated_at)
        retained["retained_from_previous_run"] = True
        merged.append(retained)
        retained_count += 1

    return dedupe_papers(merged), {
        "retained_paper_count": retained_count,
        "retained_recent_low_count": retained_recent,
        "dropped_low_relevance_count": dropped_low,
    }


def deletion_sort_key(paper: dict[str, Any]) -> tuple[int, dt.datetime]:
    level = best_match_level(paper)
    if paper.get("source_type") == "conference":
        relevance_priority = 1
    else:
        relevance_priority = 0 if level == "low" else 2
    return relevance_priority, paper_datetime(paper)


def trim_papers_for_storage(
    payload: dict[str, Any],
    max_stored_papers: int,
    max_data_bytes: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    papers = list(payload.get("papers", []))
    removed_by_level = {"high": 0, "medium": 0, "low": 0, "unknown": 0}

    def projected_size() -> int:
        projected = dict(payload)
        projected["papers"] = papers
        return json_size_bytes(projected)

    data_bytes = projected_size()
    while papers and (
        (max_stored_papers > 0 and len(papers) > max_stored_papers)
        or (max_data_bytes > 0 and data_bytes > max_data_bytes)
    ):
        remove_index = min(range(len(papers)), key=lambda index: deletion_sort_key(papers[index]))
        removed = papers.pop(remove_index)
        level = best_match_level(removed)
        removed_by_level[level if level in removed_by_level else "unknown"] += 1
        data_bytes = projected_size()

    return papers, {
        "max_stored_papers": max_stored_papers,
        "max_data_bytes": max_data_bytes,
        "data_bytes": data_bytes,
        "storage_trimmed_count": sum(removed_by_level.values()),
        "storage_trimmed_by_level": removed_by_level,
    }


def collect(
    config_path: Path,
    output_path: Path,
    conference_output_path: Path,
    days: int,
    max_per_topic: int,
    max_summaries: int,
    max_new_papers: int,
    max_stored_papers: int,
    max_new_conference_papers: int,
    max_stored_conference_papers: int,
    max_data_bytes: int,
    incremental_since_last_run: bool,
    recent_history_days: int,
    clear_cache: bool,
) -> dict[str, Any]:
    default_config = load_json(config_path)
    config = load_issue_config(default_config)

    if clear_cache and model_figure_output_dir().exists():
        try:
            shutil.rmtree(model_figure_output_dir())
        except OSError as exc:
            print(f"Warning: cannot clear model figure directory: {exc}", file=sys.stderr)
    topics = parse_topics(config)
    sources = parse_sources(config)
    now = dt.datetime.now(dt.timezone.utc)
    conference_sources = parse_conference_sources(config, now)
    active_conference_years_by_source = active_conference_years(conference_sources)
    mixed_existing_payload = {} if clear_cache else load_existing_payload(output_path)
    existing_payload, migrated_conference_payload = split_conference_payload(mixed_existing_payload)
    stored_conference_payload = {} if clear_cache else load_existing_payload(conference_output_path)
    if stored_conference_payload.get("papers"):
        existing_conference_payload = stored_conference_payload
    else:
        existing_conference_payload = migrated_conference_payload
    cached_conference_years_by_source = cached_conference_years(existing_conference_payload)
    cutoff, collection_mode = collection_cutoff(existing_payload, now, days, incremental_since_last_run)
    all_candidates = []
    successful_fetches = 0
    failed_fetches = 0
    successful_conference_fetches = 0
    failed_conference_fetches = 0
    skipped_cached_conference_years = 0
    cached_conference_candidate_count = 0
    conference_enrichment_stats: dict[str, Any] = {
        "conference_arxiv_enrichment_attempted": 0,
        "conference_arxiv_enrichment_succeeded": 0,
        "conference_arxiv_enrichment_skipped": 0,
        "conference_arxiv_enrichment_last_error": "",
    }
    source_stats: dict[str, dict[str, Any]] = {}
    source_delay_seconds = float(os.getenv("SOURCE_DELAY_SECONDS", "3"))
    for source in sources:
        source_stats[source.name] = {"type": source.type, "successful_fetches": 0, "failed_fetches": 0}
        if not source.enabled:
            continue
        if is_feed_source(source):
            print(f"Fetching feed source: {source.name}", flush=True)
            try:
                feed_papers = fetch_feed(source, max_per_topic * max(1, len(topics)))
                all_candidates.extend(feed_papers)
                successful_fetches += 1
                source_stats[source.name]["successful_fetches"] += 1
            except Exception as exc:
                failed_fetches += 1
                source_stats[source.name]["failed_fetches"] += 1
                source_stats[source.name]["last_error"] = str(exc)
                print(f"Warning: feed source failed for {source.name}: {exc}", file=sys.stderr)
            time.sleep(source_delay_seconds)
            continue

        for index, topic in enumerate(topics):
            if index:
                if source.type == "arxiv":
                    time.sleep(float(os.getenv("ARXIV_DELAY_SECONDS", "15")))
                else:
                    time.sleep(source_delay_seconds)
            print(f"Fetching {source.name} papers for topic: {topic.name}", flush=True)
            try:
                topic_papers = fetch_source_topic(source, topic, max_per_topic)
                all_candidates.extend(topic_papers)
                successful_fetches += 1
                source_stats[source.name]["successful_fetches"] += 1
            except Exception as exc:
                failed_fetches += 1
                source_stats[source.name]["failed_fetches"] += 1
                source_stats[source.name]["last_error"] = str(exc)
                print(f"Warning: {source.name} request failed for {topic.name}: {exc}", file=sys.stderr)
                if source.type == "arxiv" and should_stop_arxiv_fetches(exc):
                    skipped = len(topics) - index - 1
                    failed_fetches += skipped
                    source_stats[source.name]["failed_fetches"] += skipped
                    if skipped:
                        print(
                            f"Stopping arXiv fetches after {exc}; skipped {skipped} remaining topic(s) to avoid further throttling.",
                            file=sys.stderr,
                        )
                    break

    max_per_conference = int(os.getenv("MAX_PER_CONFERENCE", "1000"))
    conference_delay_seconds = float(os.getenv("DBLP_DELAY_SECONDS", "5"))
    for index, source in enumerate(conference_sources):
        years_to_fetch = uncached_conference_years(source, cached_conference_years_by_source)
        skipped_cached_conference_years += len(source.years) - len(years_to_fetch)
        source_stats[source.name] = {
            "type": "conference",
            "successful_fetches": 0,
            "failed_fetches": 0,
            "skipped_cached_years": len(source.years) - len(years_to_fetch),
        }
        if not years_to_fetch:
            print(f"Skipping DBLP conference source from cache: {source.name} {', '.join(str(year) for year in source.years)}", flush=True)
            continue
        if index:
            time.sleep(conference_delay_seconds)
        source_to_fetch = ConferenceSource(
            id=source.id,
            name=source.name,
            group=source.group,
            dblp_toc_patterns=source.dblp_toc_patterns,
            years=years_to_fetch,
            enabled=source.enabled,
        )
        print(f"Fetching DBLP conference papers for source: {source.name} {', '.join(str(year) for year in years_to_fetch)}", flush=True)
        try:
            source_papers = fetch_dblp_conference(source_to_fetch, max_per_conference)
            all_candidates.extend(source_papers)
            successful_fetches += 1
            successful_conference_fetches += 1
            source_stats[source.name]["successful_fetches"] += 1
        except Exception as exc:
            failed_fetches += 1
            failed_conference_fetches += 1
            source_stats[source.name]["failed_fetches"] += 1
            source_stats[source.name]["last_error"] = str(exc)
            print(f"Warning: DBLP request failed for {source.name}: {exc}", file=sys.stderr)

    for cached_paper in existing_conference_payload.get("papers", []) if isinstance(existing_conference_payload, dict) else []:
        if not isinstance(cached_paper, dict) or cached_paper.get("source_type") != "conference":
            continue
        if not should_retain_conference_paper(cached_paper, active_conference_years_by_source):
            continue
        if has_meaningful_summary(cached_paper):
            continue
        all_candidates.append(copy.deepcopy(cached_paper))
        cached_conference_candidate_count += 1

    if successful_fetches == 0 and failed_fetches > 0 and (existing_payload.get("papers") or existing_conference_payload.get("papers")):
        print("All configured sources failed; preserving existing paper data.", file=sys.stderr)

    recent_papers = []
    daily_backfill_candidates = []
    filtered_low_relevance = 0
    raw_daily_candidate_count = 0
    daily_outside_cutoff_count = 0
    backfill_days = max(days, env_int("DAILY_BACKFILL_DAYS", 14))
    daily_backfill_cutoff = now - dt.timedelta(days=max(0, backfill_days))
    for paper in dedupe_papers(all_candidates):
        is_conference_paper = paper.get("source_type") == "conference"
        if not is_conference_paper:
            raw_daily_candidate_count += 1
        activity_at = paper_activity_datetime(paper)
        in_primary_window = is_conference_paper or activity_at >= cutoff
        in_backfill_window = (
            not is_conference_paper
            and not in_primary_window
            and activity_at >= daily_backfill_cutoff
        )
        if not in_primary_window and not in_backfill_window:
            if not is_conference_paper:
                daily_outside_cutoff_count += 1
            continue

        matches = [score_paper(topic, paper) for topic in topics]
        matches.sort(key=lambda item: item["score"], reverse=True)
        best_match = matches[0]
        if not is_relevant_enough(paper, best_match):
            filtered_low_relevance += 1
            continue
        paper["matches"] = matches
        paper["best_match"] = best_match
        if in_backfill_window:
            paper["backfilled_from_recent_arxiv"] = True
            daily_outside_cutoff_count += 1
            daily_backfill_candidates.append(paper)
        else:
            recent_papers.append(paper)

    recent_papers.sort(key=lambda p: (p["best_match"]["score"], paper_activity_datetime(p)), reverse=True)
    daily_recent_papers = [paper for paper in recent_papers if paper.get("source_type") != "conference"]
    conference_recent_papers = [paper for paper in recent_papers if paper.get("source_type") == "conference"]
    daily_backfill_added_count = 0
    min_daily_papers = max(0, env_int("MIN_DAILY_PAPERS", 8))
    if len(daily_recent_papers) < min_daily_papers and daily_backfill_candidates:
        daily_backfill_candidates.sort(
            key=lambda p: (p["best_match"]["score"], paper_activity_datetime(p)),
            reverse=True,
        )
        existing_daily_ids = {str(paper.get("id", "")) for paper in daily_recent_papers}
        for paper in daily_backfill_candidates:
            if len(daily_recent_papers) >= min_daily_papers:
                break
            paper_id = str(paper.get("id", ""))
            if paper_id in existing_daily_ids:
                continue
            existing_daily_ids.add(paper_id)
            daily_recent_papers.append(paper)
            daily_backfill_added_count += 1
    candidate_paper_count = len(daily_recent_papers) + len(conference_recent_papers)
    daily_candidate_paper_count = len(daily_recent_papers)
    conference_candidate_paper_count = len(conference_recent_papers)
    if max_new_papers > 0:
        daily_recent_papers = daily_recent_papers[:max_new_papers]
    if max_new_conference_papers > 0:
        conference_recent_papers = conference_recent_papers[:max_new_conference_papers]
    conference_enrichment_stats = enrich_conference_papers_from_arxiv(conference_recent_papers)
    if conference_enrichment_stats["conference_arxiv_enrichment_succeeded"]:
        for paper in conference_recent_papers:
            matches = [score_paper(topic, paper) for topic in topics]
            matches.sort(key=lambda item: item["score"], reverse=True)
            paper["matches"] = matches
            paper["best_match"] = matches[0]
        conference_recent_papers.sort(key=lambda p: (p["best_match"]["score"], p.get("published", "")), reverse=True)
    recent_papers = sorted(
        [*daily_recent_papers, *conference_recent_papers],
        key=lambda p: (p["best_match"]["score"], paper_activity_datetime(p)),
        reverse=True,
    )
    analyses_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    llm_jobs = []
    for paper in recent_papers[:max_summaries]:
        if not should_summarize_paper_with_llm(paper):
            continue
        best_topic = next(topic for topic in topics if topic.id == paper["best_match"]["topic_id"])
        llm_jobs.append((best_topic, paper))

    if llm_enabled() and llm_jobs:
        concurrency = max(1, int(os.getenv("LLM_CONCURRENCY", "2")))
        print(f"Summarizing {len(llm_jobs)} papers with LLM using concurrency={concurrency}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(summarize_one, job) for job in llm_jobs]
            for future in concurrent.futures.as_completed(futures):
                paper_id, analysis, adjusted_match = future.result()
                analyses_by_id[paper_id] = (analysis, adjusted_match)
                print(f"Finished bilingual analysis: {paper_id}", flush=True)
    else:
        for topic, paper in llm_jobs:
            analysis, adjusted_match = summarize_with_llm(topic, paper, paper["best_match"])
            analyses_by_id[str(paper.get("id", ""))] = (analysis, adjusted_match)

    for index, paper in enumerate(recent_papers):
        paper_id = str(paper.get("id", ""))
        if index < max_summaries and paper_id in analyses_by_id:
            analysis, adjusted_match = analyses_by_id[paper_id]
            paper["ai_analysis"] = analysis
            # Keep the legacy field until the new web UI is deployed.
            paper["chinese_summary"] = analysis_to_legacy_summary(analysis)
            paper["best_match"] = adjusted_match
            paper["matches"] = [
                adjusted_match if m["topic_id"] == adjusted_match["topic_id"] else m
                for m in paper["matches"]
            ]
        else:
            analysis = fallback_analysis(paper, paper["best_match"])
            paper["ai_analysis"] = analysis
            paper["chinese_summary"] = analysis_to_legacy_summary(analysis)

    # Extract an original model/framework figure from the source PDF only after
    # relevance filtering and LLM analysis, so we do not download PDFs for the
    # entire raw candidate pool.
    model_figure_stats = enrich_model_figures(recent_papers)

    daily_recent_papers = [paper for paper in recent_papers if paper.get("source_type") != "conference"]
    conference_recent_papers = [paper for paper in recent_papers if paper.get("source_type") == "conference"]
    daily_merged_papers, daily_retention_stats = merge_with_retained_papers(
        daily_recent_papers, existing_payload, now, recent_history_days
    )
    conference_merged_papers, conference_retention_stats = merge_with_retained_papers(
        conference_recent_papers,
        existing_conference_payload,
        now,
        recent_history_days,
        active_conference_years_by_source,
    )
    daily_merged_papers.sort(key=lambda p: (p["best_match"]["score"], paper_activity_datetime(p)), reverse=True)
    conference_merged_papers.sort(key=lambda p: (p["best_match"]["score"], paper_activity_datetime(p)), reverse=True)

    base_stats = {
        "candidate_paper_count": candidate_paper_count,
        "daily_candidate_paper_count": daily_candidate_paper_count,
        "conference_candidate_paper_count": conference_candidate_paper_count,
        "raw_daily_candidate_count": raw_daily_candidate_count,
        "daily_outside_cutoff_count": daily_outside_cutoff_count,
        "daily_backfill_days": backfill_days,
        "daily_backfill_candidate_count": len(daily_backfill_candidates),
        "daily_backfill_added_count": daily_backfill_added_count,
        "min_daily_papers": min_daily_papers,
        "filtered_low_relevance_count": filtered_low_relevance,
        "days": days,
        "collection_mode": collection_mode,
        "collection_cutoff_iso": cutoff.isoformat(),
        "max_per_topic": max_per_topic,
        "max_new_papers": max_new_papers,
        "max_new_conference_papers": max_new_conference_papers,
        "sources": [source.__dict__ for source in sources],
        "conference_sources": [source.__dict__ for source in conference_sources],
        "source_stats": source_stats,
        "llm_enabled": llm_enabled(),
        "llm_concurrency": int(os.getenv("LLM_CONCURRENCY", "2")),
        "recent_history_days": recent_history_days,
        "successful_fetches": successful_fetches,
        "failed_fetches": failed_fetches,
        "successful_conference_fetches": successful_conference_fetches,
        "failed_conference_fetches": failed_conference_fetches,
        "skipped_cached_conference_years": skipped_cached_conference_years,
        "conference_source_count": len(conference_sources),
        "cached_conference_candidate_count": cached_conference_candidate_count,
        "clear_cache": clear_cache,
        **conference_enrichment_stats,
        **model_figure_stats,
    }

    payload = {
        "generated_at": email.utils.format_datetime(now),
        "generated_at_iso": now.isoformat(),
        "config_source": "issue" if config is not default_config else "file",
        "data_kind": "daily",
        "topics": [topic.__dict__ for topic in topics],
        "papers": daily_merged_papers,
        "stats": {
            **base_stats,
            "paper_count": len(daily_merged_papers),
            "new_paper_count": len(daily_recent_papers),
            **daily_retention_stats,
        },
    }
    trimmed_papers, storage_stats = trim_papers_for_storage(payload, max_stored_papers, max_data_bytes)
    trimmed_papers.sort(key=lambda p: (p["best_match"]["score"], p.get("published", "")), reverse=True)
    payload["papers"] = trimmed_papers
    payload["stats"].update(storage_stats)
    payload["stats"]["paper_count"] = len(trimmed_papers)
    payload["stats"]["data_bytes"] = json_size_bytes(payload)
    write_json(output_path, payload)

    conference_payload = {
        "generated_at": email.utils.format_datetime(now),
        "generated_at_iso": now.isoformat(),
        "config_source": "issue" if config is not default_config else "file",
        "data_kind": "conference",
        "topics": [topic.__dict__ for topic in topics],
        "papers": conference_merged_papers,
        "stats": {
            **base_stats,
            "paper_count": len(conference_merged_papers),
            "new_paper_count": len(conference_recent_papers),
            **conference_retention_stats,
        },
    }
    conference_trimmed_papers, conference_storage_stats = trim_papers_for_storage(
        conference_payload,
        max_stored_conference_papers,
        max_data_bytes,
    )
    conference_trimmed_papers.sort(key=lambda p: (p["best_match"]["score"], p.get("published", "")), reverse=True)
    conference_payload["papers"] = conference_trimmed_papers
    conference_payload["stats"].update(conference_storage_stats)
    conference_payload["stats"]["paper_count"] = len(conference_trimmed_papers)
    conference_payload["stats"]["data_bytes"] = json_size_bytes(conference_payload)
    write_json(conference_output_path, conference_payload)

    removed_figures = prune_unreferenced_model_figures(
        [*payload.get("papers", []), *conference_payload.get("papers", [])]
    )
    payload["stats"]["model_figure_pruned"] = removed_figures
    conference_payload["stats"]["model_figure_pruned"] = removed_figures
    payload["stats"]["data_bytes"] = json_size_bytes(payload)
    conference_payload["stats"]["data_bytes"] = json_size_bytes(conference_payload)
    write_json(output_path, payload)
    write_json(conference_output_path, conference_payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect papers and build static data for paper-daily.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--conference-output", type=Path, default=Path(os.getenv("CONFERENCE_OUTPUT", str(DEFAULT_CONFERENCE_OUTPUT))))
    parser.add_argument("--days", type=int, default=int(os.getenv("LOOKBACK_DAYS", "7")))
    parser.add_argument("--max-per-topic", type=int, default=int(os.getenv("MAX_PER_TOPIC", "25")))
    parser.add_argument("--max-summaries", type=int, default=int(os.getenv("MAX_SUMMARIES", "40")))
    parser.add_argument("--max-new-papers", type=int, default=int(os.getenv("MAX_NEW_PAPERS", str(DEFAULT_MAX_NEW_PAPERS))))
    parser.add_argument("--max-stored-papers", type=int, default=int(os.getenv("MAX_STORED_PAPERS", str(DEFAULT_MAX_STORED_PAPERS))))
    parser.add_argument(
        "--max-new-conference-papers",
        type=int,
        default=int(os.getenv("MAX_NEW_CONFERENCE_PAPERS", str(DEFAULT_MAX_NEW_CONFERENCE_PAPERS))),
    )
    parser.add_argument(
        "--max-stored-conference-papers",
        type=int,
        default=int(os.getenv("MAX_STORED_CONFERENCE_PAPERS", str(DEFAULT_MAX_STORED_CONFERENCE_PAPERS))),
    )
    parser.add_argument("--max-data-bytes", type=int, default=int(os.getenv("MAX_DATA_BYTES", str(DEFAULT_MAX_DATA_BYTES))))
    parser.add_argument("--incremental-since-last-run", action="store_true", default=env_flag("INCREMENTAL_SINCE_LAST_RUN"))
    parser.add_argument("--recent-history-days", type=int, default=int(os.getenv("RECENT_HISTORY_DAYS", str(DEFAULT_RECENT_HISTORY_DAYS))))
    parser.add_argument("--clear-cache", action="store_true", default=env_flag("CLEAR_PAPER_CACHE"))
    args = parser.parse_args()
    payload = collect(
        args.config,
        args.output,
        args.conference_output,
        args.days,
        args.max_per_topic,
        args.max_summaries,
        args.max_new_papers,
        args.max_stored_papers,
        args.max_new_conference_papers,
        args.max_stored_conference_papers,
        args.max_data_bytes,
        args.incremental_since_last_run,
        args.recent_history_days,
        args.clear_cache,
    )
    print(f"Wrote {len(payload['papers'])} daily papers to {args.output}")
    stats = payload.get("stats", {})
    print(
        "Daily arXiv stats: "
        f"raw={stats.get('raw_daily_candidate_count', 0)}, "
        f"selected={stats.get('daily_candidate_paper_count', 0)}, "
        f"backfilled={stats.get('daily_backfill_added_count', 0)}, "
        f"filtered={stats.get('filtered_low_relevance_count', 0)}"
    )
    if args.conference_output.exists():
        conference_payload = load_json(args.conference_output)
        print(f"Wrote {len(conference_payload.get('papers', []))} conference papers to {args.conference_output}")


if __name__ == "__main__":
    main()
