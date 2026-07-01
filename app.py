import os
import re
import json
import csv
import time
import hashlib
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from dotenv import load_dotenv, find_dotenv

env_path = find_dotenv()
if env_path:
    load_dotenv(dotenv_path=env_path)

RESULTS_PER_PAGE = 10
DEFAULT_SEARCH_TIMEOUT = 30
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_MAX_FETCH_BYTES = 5 * 1024 * 1024
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")


@dataclass
class ScrapeResult:
    query: str
    title: str
    link: str
    snippet: str
    mime: str = ""
    page_number: int = 0
    search_rank: int = 0
    source_rank: int = 0
    status: str = ""
    saved_as: str = ""
    error: str = ""
    http_status: Optional[int] = None
    content_type: str = ""
    content_length: Optional[int] = None
    fetched_at: str = ""
    sha256: str = ""
    final_url: str = ""
    page_title: str = ""
    meta_description: str = ""
    word_count: int = 0
    char_count: int = 0
    text: str = ""


@dataclass
class SearchError:
    query: str
    page_number: int
    error_type: str
    message: str
    http_status: Optional[int] = None


def _parse_queries(raw: str) -> List[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    return [q.strip() for q in raw.split(",") if q.strip()]


def _parse_domain_list(raw: str) -> List[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            return [str(x).strip().lower() for x in arr if str(x).strip()]
        except Exception:
            pass
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def _int_env(name: str, default: int) -> int:
    v = os.getenv(name, "")
    try:
        return int(v) if v.strip() else default
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    v = os.getenv(name, "")
    try:
        return float(v) if v.strip() else default
    except Exception:
        return default


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\s\-.()]+", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "document"


def short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def guess_filename_from_url(url: str) -> str:
    try:
        name = Path(urlparse(url).path).name
        return safe_filename(Path(name).stem) or "page"
    except Exception:
        return "page"


def normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        tracking_params = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "gclid",
            "fbclid",
        }
        kept_params = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k not in tracking_params
        ]
        kept_params.sort()
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            query=urlencode(kept_params),
            fragment="",
        )
        return urlunparse(normalized)
    except Exception:
        return url.strip()


def extract_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_blocked_domain(url: str, blocked_domains: List[str]) -> bool:
    hostname = extract_hostname(url)
    if not hostname:
        return False

    for blocked in blocked_domains:
        blocked = blocked.lower().strip()
        if not blocked:
            continue
        if hostname == blocked or hostname.endswith(f".{blocked}"):
            return True

    return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_html_content_type(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return any(ct in lowered for ct in HTML_CONTENT_TYPES)


def build_session(user_agent: str) -> Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    retry = Retry(
        total=2,
        connect=2,
        read=1,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("content_finder")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(file_fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    return logger


def classify_google_error(
    response: Optional[Response], data: Optional[Dict[str, Any]], exc: Optional[Exception]
) -> Tuple[str, str, Optional[int]]:
    if response is not None:
        status = response.status_code
        try:
            err_obj = (data or {}).get("error", {})
            message = err_obj.get("message") or response.text[:500]
        except Exception:
            message = response.text[:500]

        lowered = (message or "").lower()

        if status == 403 and ("quota" in lowered or "limit" in lowered):
            return "quota_exceeded", message, status
        if status == 403 and ("key" in lowered or "credential" in lowered or "access" in lowered):
            return "auth_error", message, status
        if status == 400:
            return "bad_request", message, status
        if status == 429:
            return "rate_limited", message, status
        if 500 <= status <= 599:
            return "server_error", message, status
        return "http_error", message, status

    if exc is not None:
        msg = str(exc)
        lowered = msg.lower()
        if "timeout" in lowered:
            return "timeout", msg, None
        if "connection" in lowered:
            return "connection_error", msg, None
        return "request_error", msg, None

    return "unknown_error", "Unknown error", None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search Google Custom Search results and scrape the readable text of each landing page."
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Query to search. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Maximum number of Google CSE result pages to request per query.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Delay between requests in seconds.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Request timeout in seconds for page fetches.",
    )
    parser.add_argument(
        "--search-timeout",
        type=int,
        default=None,
        help="Request timeout in seconds for Google CSE search calls.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of concurrent scrape workers.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Maximum bytes to read per page (0 disables the cap).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Root output folder; manifests are written at its root and .txt files in <output>/pages.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file name or absolute path.",
    )
    parser.add_argument(
        "--api-endpoint",
        default=None,
        help="Google CSE JSON API endpoint.",
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help="User-Agent to use for requests.",
    )
    parser.add_argument(
        "--blocked-domain",
        action="append",
        dest="blocked_domains",
        help="Domain to avoid scraping. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and write manifests without fetching pages.",
    )
    return parser


def load_config(args: argparse.Namespace) -> Dict[str, Any]:
    api_key = os.getenv("API_KEY")
    cx = os.getenv("CX")

    if not api_key or not cx:
        raise SystemExit("Missing required values in .env or environment (API_KEY and CX are mandatory).")

    env_queries = _parse_queries(os.getenv("QUERIES", ""))
    cli_queries = args.queries or []
    queries = cli_queries if cli_queries else env_queries

    if not queries:
        raise SystemExit("No queries provided. Use --query or set QUERIES in .env.")

    env_blocked_domains = _parse_domain_list(os.getenv("BLOCKED_SCRAPE_DOMAINS", ""))
    cli_blocked_domains = [d.strip().lower() for d in (args.blocked_domains or []) if d.strip()]
    blocked_domains = cli_blocked_domains if cli_blocked_domains else env_blocked_domains

    pages = args.pages if args.pages is not None else _int_env("PAGES", 10)
    delay = args.delay if args.delay is not None else _float_env("DELAY", 0.0)
    timeout = args.timeout if args.timeout is not None else _int_env("TIMEOUT", 30)
    search_timeout = (
        args.search_timeout
        if args.search_timeout is not None
        else _int_env("SEARCH_TIMEOUT", DEFAULT_SEARCH_TIMEOUT)
    )
    workers = max(1, args.workers if args.workers is not None else _int_env("WORKERS", 4))
    max_bytes = (
        args.max_bytes
        if args.max_bytes is not None
        else _int_env("MAX_FETCH_BYTES", DEFAULT_MAX_FETCH_BYTES)
    )

    api_endpoint = (args.api_endpoint or os.getenv("API_ENDPOINT", "https://www.googleapis.com/customsearch/v1")).strip()
    output_dir = Path((args.output_dir or os.getenv("OUTPUT_DIR", "output")).strip() or "output")
    text_dir = output_dir / "pages"
    manifest_dir = output_dir
    log_file = (args.log_file or os.getenv("LOG_FILE", "content_finder.log")).strip() or "content_finder.log"
    user_agent = (
        args.user_agent
        or os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (compatible; content-finder/1.0; +https://example.com/bot)",
        )
    ).strip()

    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = output_dir / log_path

    return {
        "API_KEY": api_key,
        "CX": cx,
        "API_ENDPOINT": api_endpoint,
        "OUTPUT_DIR": output_dir,
        "TEXT_DIR": text_dir,
        "MANIFEST_DIR": manifest_dir,
        "LOG_PATH": log_path,
        "USER_AGENT": user_agent,
        "QUERIES": queries,
        "BLOCKED_SCRAPE_DOMAINS": blocked_domains,
        "PAGES": pages,
        "DELAY": delay,
        "TIMEOUT": timeout,
        "SEARCH_TIMEOUT": search_timeout,
        "WORKERS": workers,
        "MAX_FETCH_BYTES": max_bytes,
        "DRY_RUN": args.dry_run,
    }


def search_web(
    session: Session,
    logger: logging.Logger,
    api_key: str,
    cx: str,
    api_endpoint: str,
    query: str,
    pages: int,
    delay: float,
    search_timeout: int,
) -> Tuple[List[ScrapeResult], List[SearchError]]:
    logger.info("Starting search for query: %s (pages=%d)", query, pages)
    results: List[ScrapeResult] = []
    errors: List[SearchError] = []
    start = 1
    rank = 0

    for page in range(1, pages + 1):
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": RESULTS_PER_PAGE,
            "start": start,
            "safe": "off",
        }

        logger.info(
            "Requesting Google CSE page %d for query '%s' (start=%d)",
            page,
            query,
            start,
        )

        response: Optional[Response] = None
        data: Optional[Dict[str, Any]] = None

        try:
            response = session.get(api_endpoint, params=params, timeout=search_timeout)

            try:
                data = response.json()
            except Exception:
                data = None

            if response.status_code != 200:
                err_type, err_msg, http_status = classify_google_error(response, data, None)
                logger.error(
                    "Search failed for query='%s', page=%d, type=%s, status=%s, message=%s",
                    query,
                    page,
                    err_type,
                    http_status,
                    err_msg,
                )
                errors.append(
                    SearchError(
                        query=query,
                        page_number=page,
                        error_type=err_type,
                        message=err_msg,
                        http_status=http_status,
                    )
                )
                break

            items = (data or {}).get("items", [])
            logger.info("Received %d items for query '%s' on page %d", len(items), query, page)

            for idx, item in enumerate(items, start=1):
                rank += 1
                results.append(
                    ScrapeResult(
                        query=query,
                        title=item.get("title", ""),
                        link=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                        mime=item.get("mime", ""),
                        page_number=page,
                        search_rank=rank,
                        source_rank=idx,
                    )
                )

            next_page = (data or {}).get("queries", {}).get("nextPage", [{}])[0].get("startIndex")
            if not next_page:
                logger.info("No more pages for query '%s'", query)
                break

            start = next_page
            if delay:
                time.sleep(delay)

        except Exception as exc:
            err_type, err_msg, http_status = classify_google_error(response, data, exc)
            logger.error(
                "Search exception for query='%s', page=%d, type=%s, message=%s",
                query,
                page,
                err_type,
                err_msg,
            )
            errors.append(
                SearchError(
                    query=query,
                    page_number=page,
                    error_type=err_type,
                    message=err_msg,
                    http_status=http_status,
                )
            )
            break

    logger.info("Finished search for query '%s' with %d total items", query, len(results))
    return results, errors


def dedupe_results(results: List[ScrapeResult], logger: logging.Logger) -> List[ScrapeResult]:
    logger.info("Deduplicating %d results by normalized link", len(results))
    seen: set[str] = set()
    out: List[ScrapeResult] = []

    for item in results:
        normalized = normalize_url(item.link)
        if normalized not in seen:
            seen.add(normalized)
            item.link = normalized
            out.append(item)

    logger.info("Deduplication complete: %d unique links", len(out))
    return out


def choose_output_path(out_dir: Path, title_hint: str, url: str) -> Path:
    base = safe_filename(title_hint) or guess_filename_from_url(url)
    suffix = short_hash(url, 8)
    filename = f"{base}_{suffix}.txt"
    return out_dir / filename


def _meta_content(soup: BeautifulSoup, **attrs: str) -> str:
    tag = soup.find("meta", attrs=attrs)
    if tag:
        return (tag.get("content") or "").strip()
    return ""


def extract_content(html: bytes) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    page_title = ""
    if soup.title and soup.title.string:
        page_title = soup.title.string.strip()
    if not page_title:
        page_title = _meta_content(soup, property="og:title")

    meta_description = _meta_content(soup, name="description")
    if not meta_description:
        meta_description = _meta_content(soup, property="og:description")

    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()

    container = soup.find("main") or soup.find("article") or soup.body or soup
    raw_text = container.get_text(separator="\n")

    lines = [line.strip() for line in raw_text.splitlines()]
    text = "\n".join(line for line in lines if line)

    return {
        "page_title": page_title,
        "meta_description": meta_description,
        "text": text,
    }


def _scrape_result(
    error: str = "",
    http_status: Optional[int] = None,
    content_type: str = "",
    content_length: Optional[int] = None,
    final_url: str = "",
    sha256: str = "",
    page_title: str = "",
    meta_description: str = "",
    text: str = "",
) -> Dict[str, Any]:
    return {
        "error": error,
        "http_status": http_status,
        "content_type": content_type,
        "content_length": content_length,
        "final_url": final_url,
        "sha256": sha256,
        "page_title": page_title,
        "meta_description": meta_description,
        "text": text,
    }


def scrape_page(
    session: Session,
    logger: logging.Logger,
    timeout: int,
    max_bytes: int,
    url: str,
) -> Tuple[bool, Dict[str, Any]]:
    logger.info("Fetching page: url=%s", url)
    request_timeout = (min(DEFAULT_CONNECT_TIMEOUT, timeout), timeout)

    try:
        with session.get(url, stream=True, timeout=request_timeout, allow_redirects=True) as response:
            final_url = response.url
            http_status = response.status_code
            content_type = response.headers.get("Content-Type", "")
            content_length_raw = response.headers.get("Content-Length")
            content_length = int(content_length_raw) if content_length_raw and content_length_raw.isdigit() else None

            meta = dict(
                http_status=http_status,
                content_type=content_type,
                content_length=content_length,
                final_url=final_url,
            )

            if http_status != 200:
                msg = f"HTTP {http_status}"
                logger.warning("Fetch failed (%s) for url=%s", msg, url)
                return False, _scrape_result(error=msg, **meta)

            if not is_html_content_type(content_type):
                msg = f"Not HTML (Content-Type={content_type or 'unknown'})"
                logger.warning("Fetch skipped: %s; url=%s", msg, url)
                return False, _scrape_result(error=msg, **meta)

            chunks: List[bytes] = []
            total = 0
            truncated = False
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if max_bytes and total >= max_bytes:
                    truncated = True
                    break

            raw = b"".join(chunks)
            if not raw:
                msg = "Empty response body"
                logger.warning("Fetch failed (%s) for url=%s", msg, url)
                return False, _scrape_result(error=msg, **meta)

            if truncated:
                logger.info("Page truncated at %d bytes: url=%s", total, url)

            sha256_hex = hashlib.sha256(raw).hexdigest()
            extracted = extract_content(raw)

            if not extracted["text"]:
                msg = "No readable text extracted"
                logger.warning("Fetch produced no text: url=%s", url)
                return False, _scrape_result(error=msg, sha256=sha256_hex, **meta)

            logger.info("Fetch succeeded: url=%s (%d chars)", url, len(extracted["text"]))
            return True, _scrape_result(
                sha256=sha256_hex,
                page_title=extracted["page_title"],
                meta_description=extracted["meta_description"],
                text=extracted["text"],
                **meta,
            )

    except Exception as exc:
        logger.error("Exception while fetching url=%s: %s", url, exc)
        return False, _scrape_result(error=str(exc))


def write_text_file(out_dir: Path, item: ScrapeResult) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = choose_output_path(out_dir, item.page_title or item.title, item.link)

    header_lines = [
        f"Title: {item.page_title or item.title}",
        f"URL: {item.link}",
        f"Final URL: {item.final_url}",
        f"Query: {item.query}",
        f"Fetched: {item.fetched_at}",
        f"Description: {item.meta_description}",
        f"Words: {item.word_count}  Characters: {item.char_count}",
        "=" * 72,
        "",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(header_lines))
        f.write(item.text)
        f.write("\n")

    return path


def save_manifest(
    manifest_dir: Path,
    logger: logging.Logger,
    data: List[ScrapeResult],
    search_errors: List[SearchError],
) -> None:
    manifest_dir.mkdir(parents=True, exist_ok=True)

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = manifest_dir / f"scrape_results_{run_stamp}.json"
    csv_path = manifest_dir / f"scrape_results_{run_stamp}.csv"
    text_path = manifest_dir / f"scrape_results_{run_stamp}.txt"
    errors_path = manifest_dir / f"search_errors_{run_stamp}.json"

    summary = {
        "generated_at": utc_now_iso(),
        "total_results": len(data),
        "scraped": sum(1 for x in data if x.status == "scraped"),
        "skipped": sum(1 for x in data if x.status == "skipped"),
        "total_words": sum(x.word_count for x in data),
        "search_error_count": len(search_errors),
    }

    payload = {
        "summary": summary,
        "results": [asdict(row) for row in data],
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    fields = [
        "query",
        "title",
        "link",
        "snippet",
        "page_number",
        "search_rank",
        "source_rank",
        "status",
        "saved_as",
        "error",
        "http_status",
        "content_type",
        "content_length",
        "fetched_at",
        "sha256",
        "final_url",
        "page_title",
        "meta_description",
        "word_count",
        "char_count",
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in data:
            writer.writerow({k: getattr(row, k) for k in fields})

    with open(errors_path, "w", encoding="utf-8") as f:
        json.dump([asdict(err) for err in search_errors], f, indent=2, ensure_ascii=False)

    scraped_rows = [row for row in data if row.status == "scraped" and row.text]
    with open(text_path, "w", encoding="utf-8") as f:
        f.write("Combined scraped text\n")
        f.write(f"Generated: {summary['generated_at']}\n")
        f.write(f"Pages: {len(scraped_rows)}  Total words: {sum(r.word_count for r in scraped_rows)}\n")
        for row in scraped_rows:
            f.write("\n")
            f.write("=" * 80 + "\n")
            f.write(f"Title: {row.page_title or row.title}\n")
            f.write(f"URL: {row.link}\n")
            f.write(f"Query: {row.query}\n")
            f.write(f"Words: {row.word_count}  Characters: {row.char_count}\n")
            f.write("=" * 80 + "\n\n")
            f.write(row.text)
            f.write("\n")

    logger.info("Saved manifest JSON: %s", json_path)
    logger.info("Saved manifest CSV: %s", csv_path)
    logger.info("Saved combined text: %s", text_path)
    logger.info("Saved search errors JSON: %s", errors_path)


def run_searches(
    session: Session,
    logger: logging.Logger,
    config: Dict[str, Any],
) -> Tuple[List[ScrapeResult], List[SearchError]]:
    all_results: List[ScrapeResult] = []
    search_errors: List[SearchError] = []

    for q in config["QUERIES"]:
        logger.info("[search] %s", q)

        hits, errs = search_web(
            session=session,
            logger=logger,
            api_key=config["API_KEY"],
            cx=config["CX"],
            api_endpoint=config["API_ENDPOINT"],
            query=q,
            pages=config["PAGES"],
            delay=config["DELAY"],
            search_timeout=config["SEARCH_TIMEOUT"],
        )

        all_results.extend(hits)
        search_errors.extend(errs)

        logger.info("  -> %d results", len(hits))
        for err in errs:
            logger.warning("  -> search error [%s]: %s", err.error_type, err.message)

    return all_results, search_errors


def run_scrapes(
    session: Session,
    logger: logging.Logger,
    config: Dict[str, Any],
    all_results: List[ScrapeResult],
) -> None:
    total = len(all_results)
    out_dir = config["TEXT_DIR"]
    timeout = config["TIMEOUT"]
    delay = config["DELAY"]
    workers = config["WORKERS"]
    max_bytes = config["MAX_FETCH_BYTES"]
    blocked_domains = config["BLOCKED_SCRAPE_DOMAINS"]

    def process(item: ScrapeResult) -> None:
        if is_blocked_domain(item.link, blocked_domains):
            blocked_host = extract_hostname(item.link)
            item.status = "skipped"
            item.error = f"Blocked domain: {blocked_host}"
            item.fetched_at = utc_now_iso()
            logger.info("Skipped blocked domain: url=%s, hostname=%s", item.link, blocked_host)
            return

        if delay:
            time.sleep(delay)

        ok, info = scrape_page(
            session=session,
            logger=logger,
            timeout=timeout,
            max_bytes=max_bytes,
            url=item.link,
        )

        item.http_status = info.get("http_status")
        item.content_type = info.get("content_type", "")
        item.content_length = info.get("content_length")
        item.final_url = info.get("final_url", "")
        item.sha256 = info.get("sha256", "")
        item.page_title = info.get("page_title", "")
        item.meta_description = info.get("meta_description", "")
        item.text = info.get("text", "")
        item.char_count = len(item.text)
        item.word_count = len(item.text.split())
        item.fetched_at = utc_now_iso()

        if ok:
            item.status = "scraped"
            item.saved_as = str(write_text_file(out_dir, item))
        else:
            item.status = "skipped"
            item.error = info.get("error", "")

    def log_progress(done: int, item: ScrapeResult) -> None:
        detail = item.saved_as if item.status == "scraped" else item.error
        logger.info("[%d/%d] %s: %s (%s)", done, total, item.status, item.link, detail)

    if workers > 1 and total > 1:
        logger.info("Scraping with %d concurrent workers", workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, item): item for item in all_results}
            for done, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    item.status = "skipped"
                    item.error = str(exc)
                    item.fetched_at = utc_now_iso()
                    logger.error("Scrape task error: url=%s: %s", item.link, exc)
                log_progress(done, item)
    else:
        for done, item in enumerate(all_results, start=1):
            process(item)
            log_progress(done, item)


def print_summary(
    logger: logging.Logger,
    config: Dict[str, Any],
    all_results: List[ScrapeResult],
    search_errors: List[SearchError],
) -> None:
    scraped_count = sum(1 for x in all_results if x.status == "scraped")
    skipped_count = sum(1 for x in all_results if x.status == "skipped")

    logger.info(
        "Summary: total=%d scraped=%d skipped=%d search_errors=%d",
        len(all_results),
        scraped_count,
        skipped_count,
        len(search_errors),
    )
    logger.info("Text files saved in: %s", config["TEXT_DIR"].resolve())
    logger.info("=== Run finished ===\n")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = load_config(args)

    logger = setup_logger(config["LOG_PATH"])
    session = build_session(config["USER_AGENT"])

    logger.info("=== Run started ===")
    logger.info("Queries: %s", config["QUERIES"])
    logger.info("Blocked scrape domains: %s", config["BLOCKED_SCRAPE_DOMAINS"])
    logger.info("Output directory: %s", config["OUTPUT_DIR"].resolve())
    logger.info("Text files directory: %s", config["TEXT_DIR"].resolve())
    logger.info("Log file: %s", config["LOG_PATH"].resolve())
    logger.info("Workers: %d", config["WORKERS"])
    logger.info("Dry run: %s", config["DRY_RUN"])

    all_results, search_errors = run_searches(session, logger, config)

    all_results = dedupe_results(all_results, logger)
    logger.info("[dedupe] %d unique links", len(all_results))

    if not config["DRY_RUN"]:
        run_scrapes(session, logger, config, all_results)
    else:
        logger.info("Dry run enabled; skipping page fetches.")
        for item in all_results:
            item.status = "not_scraped"

    save_manifest(
        manifest_dir=config["MANIFEST_DIR"],
        logger=logger,
        data=all_results,
        search_errors=search_errors,
    )

    print_summary(logger, config, all_results, search_errors)


if __name__ == "__main__":
    main()
