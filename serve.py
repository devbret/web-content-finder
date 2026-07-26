import json
import os
import posixpath
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "claude-opus-5")
MAX_BODY_BYTES = 20 * 1024 * 1024
STREAM_SENTINEL = "\x1e"

ALLOWED_FILES = {"/", "/index.html", "/main.css", "/main.js"}
ALLOWED_PREFIXES = ("/vendor/", "/output/")

ADAPTIVE_THINKING_MODELS = (
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
)

anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = (
    "You are a sharp research analyst. You are given the readable text that a "
    "web-scraping tool extracted from pages it found via Google search queries, "
    "along with metadata about the scrape runs the pages came from.\n\n"
    "Write an analysis of the scraped content as a set. Structure it in "
    "markdown with '##' section headings, and keep it concrete - refer to "
    "pages by their titles. Cover:\n"
    "1. Main themes - the topics and narratives the pages form, and which "
    "pages belong to each.\n"
    "2. How the queries compare - what each search query surfaced, where they "
    "overlap, and where they diverge.\n"
    "3. Source landscape - what kinds of sites dominate (commercial, "
    "educational, news, forums), and what that means for the results.\n"
    "4. Notable findings - the most substantive or surprising facts and "
    "claims in the text, attributed to their pages.\n"
    "5. Outliers and noise - pages that do not fit the queries' intent, and "
    "whether they look like search noise or an interesting tangent.\n"
    "6. Gaps and next steps - what the scrape did not cover, with concrete "
    "follow-up queries worth running.\n\n"
    "If the set is small or one-note, say so plainly rather than padding the "
    "analysis. Do not invent facts beyond the provided text; excerpts are "
    "often truncated, so note uncertainty where an excerpt is too thin to "
    "support a claim."
)


def build_user_prompt(payload):
    pages = payload.get("pages") or []
    runs = payload.get("runs") or []
    context = payload.get("context") or {}

    lines = [
        "Here are the scraped pages currently in view in the explorer, as "
        "JSON. `query` is the search that surfaced the page, `excerpt` is the "
        "extracted page text (usually truncated), and `word_count` is the "
        "full extracted length.",
        "",
        "VIEW CONTEXT (the filters the user applied before submitting):",
        json.dumps(context, indent=2),
        "",
        "SCRAPE RUNS:",
        json.dumps(runs, indent=2),
        "",
        f"SCRAPED PAGES ({len(pages)}):",
        json.dumps(pages, indent=2),
    ]
    return "\n".join(lines)


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _path_allowed(self):
        path = posixpath.normpath(unquote(urlsplit(self.path).path)) or "/"
        if path in ALLOWED_FILES:
            return True
        if any(part.startswith(".") for part in path.split("/") if part):
            return False
        return path.startswith(ALLOWED_PREFIXES)

    def do_GET(self):
        if not self._path_allowed():
            self.send_error(404, "Not found")
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._path_allowed():
            self.send_error(404, "Not found")
            return
        super().do_HEAD()

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/analyze":
            self._send_json(404, {"error": "Unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(400, {"error": "Missing or oversized request body"})
                return
            payload = json.loads(self.rfile.read(length))
            pages = payload.get("pages") if isinstance(payload, dict) else None
            if not isinstance(pages, list) or not pages:
                self._send_json(400, {"error": "pages must be a non-empty list"})
                return
            if not isinstance(payload.get("runs") or [], list):
                payload["runs"] = []
            if not isinstance(payload.get("context") or {}, dict):
                payload["context"] = {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        if not os.environ.get("ANTHROPIC_API_KEY"):
            self._send_json(500, {"error": "ANTHROPIC_API_KEY is not set in .env"})
            return

        request_kwargs = {
            "model": ANALYSIS_MODEL,
            "max_tokens": 64000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(payload)}],
        }
        if ANALYSIS_MODEL.startswith(ADAPTIVE_THINKING_MODELS):
            request_kwargs["thinking"] = {"type": "adaptive"}

        try:
            stream_ctx = anthropic_client.messages.stream(**request_kwargs)
            stream = stream_ctx.__enter__()
        except Exception as exc:
            self._send_json(502, {"error": f"Claude request failed: {exc}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Analysis-Model", ANALYSIS_MODEL)
        self.end_headers()
        try:
            for text in stream.text_stream:
                self.wfile.write(text.encode("utf-8"))
                self.wfile.flush()
            final = stream.get_final_message()
            trailer = STREAM_SENTINEL + json.dumps(
                {"stop_reason": final.stop_reason}
            )
            self.wfile.write(trailer.encode("utf-8"))
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                message = f"\n\n[Analysis interrupted: {exc}]"
                self.wfile.write(message.encode("utf-8"))
            except OSError:
                pass
        finally:
            stream_ctx.__exit__(None, None, None)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Warning: ANTHROPIC_API_KEY is not set - the explorer will work, "
            "but the Claude analysis button will fail until you add it to .env."
        )
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Explorer: http://localhost:{port}/  (analysis model: {ANALYSIS_MODEL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
