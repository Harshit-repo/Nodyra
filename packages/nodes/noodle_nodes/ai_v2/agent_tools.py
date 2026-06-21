"""AI agent power-tool nodes + sub-agent node — Phase B.

Each tool node outputs a ToolAdapter on an ``ai_tool`` port (same pattern as
AI HTTP Tool). ``ai_sub_agent`` outputs a SubAgentAdapter on ``ai_subagent``.
Heavy deps (simpleeval, playwright) are lazy-imported with friendly errors.
"""

from __future__ import annotations

import ast
import html
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any

import httpx

from noodle_nodes.http_security import assert_public_http_url, safe_request

from noodle.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from noodle.sdk import node

AI_CATEGORY = "AI"

# Names/functions exposed to the calculator. factorial is wrapped to cap input.
_SAFE_MATH_NAMES: dict[str, Any] = {
    "pi": math.pi, "e": math.e, "inf": math.inf, "nan": math.nan,
}


def _capped_factorial(n: Any) -> int:
    value = int(n)
    if value < 0 or value > 1000:
        raise ValueError("factorial argument must be between 0 and 1000")
    return math.factorial(value)


_SAFE_MATH_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
    "factorial": _capped_factorial, "gcd": math.gcd, "degrees": math.degrees,
    "radians": math.radians, "sum": sum, "min": min, "max": max,
}

_BLOCKED_SUBSTRINGS = ("__", "import", "exec", "eval", "open", "lambda")


class CalculatorToolAdapter(ToolAdapter):
    """Safe math expression evaluator backed by simpleeval (no eval/exec)."""

    def __init__(self, *, name: str, description: str, precision: int, allow_complex: bool) -> None:
        self._name = name or "calculate"
        self._description = description or "Evaluate a mathematical expression safely."
        self._precision = max(0, min(15, int(precision or 10)))
        self._allow_complex = bool(allow_complex)

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=ToolParameterSchema(
                properties={"expression": {"type": "string", "description": "Math expression to evaluate."}},
                required=["expression"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return False

    def invoke(self, arguments: dict[str, Any]) -> str:
        expression = str((arguments or {}).get("expression") or "").strip()
        if not expression:
            return json.dumps({"error": "No expression provided"})
        lowered = expression.lower()
        if any(token in lowered for token in _BLOCKED_SUBSTRINGS):
            return json.dumps({"error": "Expression contains a blocked construct"})
        try:
            from simpleeval import SimpleEval
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "AI Calculator Tool requires simpleeval. Add simpleeval>=0.9 to "
                "the workflow environment, rebuild, then run again."
            ) from exc
        evaluator = SimpleEval(functions=_SAFE_MATH_FUNCS, names=_SAFE_MATH_NAMES)
        try:
            value = evaluator.eval(expression)
        except ZeroDivisionError:
            return json.dumps({"error": "Division by zero"})
        except OverflowError:
            return json.dumps({"error": "Result too large to represent"})
        except (ValueError, TypeError, KeyError, SyntaxError, NameError) as exc:
            return json.dumps({"error": f"Invalid expression: {exc}"})
        if isinstance(value, complex):
            if not self._allow_complex:
                return json.dumps({"error": "Result is complex. Enable allow_complex or reformulate."})
            return json.dumps({"result": str(value), "expression": expression})
        if isinstance(value, float):
            if math.isnan(value):
                return json.dumps({"result": None, "expression": expression, "note": "Result is not a number"})
            if math.isinf(value):
                return json.dumps({"result": None, "expression": expression, "note": "Result is infinite"})
            value = round(value, self._precision)
        try:
            return json.dumps({"result": value, "expression": expression})
        except ValueError as exc:
            return json.dumps({"error": f"Result too large to represent: {exc}"})


@node(
    name="AI Calculator Tool",
    id="ai_calculator_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    requirements=["simpleeval>=0.9"],
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "precision": {"description": "Decimal places in the result (0-15)."},
        "allow_complex": {"widget": "toggle", "description": "Allow complex-number results."},
    },
)
def ai_calculator_tool(
    name: str = "calculate",
    description: str = "Evaluate a mathematical expression safely.",
    precision: int = 10,
    allow_complex: bool = False,
) -> ToolAdapter:
    """Supply a safe math-evaluation tool to a downstream AI Agent."""
    return CalculatorToolAdapter(
        name=name, description=description, precision=precision, allow_complex=allow_complex
    )


# ---------------------------------------------------------------------------
# Code Execution Tool
# ---------------------------------------------------------------------------

_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DANGEROUS_ATTRS = {"__class__", "__bases__", "__subclasses__", "__globals__", "__builtins__", "__mro__"}


def _ast_security_check(code: str, allowed_modules: set[str]) -> None:
    """Raise PermissionError if the code uses a blocked construct.

    Import checking: every import's root module must be present in
    *allowed_modules*.  Passing an empty set blocks all imports; passing a
    non-empty set allows only the listed roots.

    Dangerous builtins (eval/exec/compile/__import__) and dunder attribute
    access are always blocked regardless of the allowlist.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PermissionError(f"code does not parse: {exc}") from exc
    for nodeobj in ast.walk(tree):
        if isinstance(nodeobj, ast.Import):
            for alias in nodeobj.names:
                root = alias.name.split(".")[0]
                if root not in allowed_modules:
                    raise PermissionError(f"blocked import: {alias.name}")
        elif isinstance(nodeobj, ast.ImportFrom):
            root = (nodeobj.module or "").split(".")[0]
            if root not in allowed_modules:
                raise PermissionError(f"blocked import: {nodeobj.module}")
        elif isinstance(nodeobj, ast.Call):
            func = nodeobj.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_CALLS:
                raise PermissionError(f"blocked call: {func.id}")
        elif isinstance(nodeobj, ast.Attribute):
            if nodeobj.attr in _DANGEROUS_ATTRS:
                raise PermissionError(f"blocked attribute access: {nodeobj.attr}")


class CodeExecToolAdapter(ToolAdapter):
    """Runs Python/JS in an isolated subprocess with an AST pre-check."""

    def __init__(self, *, name: str, description: str, language: str,
                 allowed_modules: str, timeout_seconds: int, max_output_chars: int) -> None:
        self._name = name or "run_code"
        self._description = description or "Run code and return stdout."
        self._language = (language or "python").lower()
        # None means "no import restriction"; a set (even empty) enforces an allowlist.
        parsed = {m.strip() for m in str(allowed_modules or "").split(",") if m.strip()}
        self._allowed: set[str] | None = parsed if parsed else None
        self._timeout = max(1, min(300, int(timeout_seconds or 30)))
        self._max_output = max(1, int(max_output_chars or 8000))

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=(
                f"{self._description} Code must print() its results to stdout; "
                "return values are not captured. Files written are discarded after the call."
            ),
            parameters=ToolParameterSchema(
                properties={
                    "code": {"type": "string", "description": "Source code to execute."},
                    "timeout": {"type": "integer", "description": "Optional timeout seconds (<= node limit)."},
                },
                required=["code"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return True

    def invoke(self, arguments: dict[str, Any]) -> str:
        code = str((arguments or {}).get("code") or "")
        if not code.strip():
            return json.dumps({"error": "No code provided"})
        requested = arguments.get("timeout")
        timeout = self._timeout
        if isinstance(requested, (int, float)) and 0 < int(requested) <= self._timeout:
            timeout = int(requested)
        if self._language == "python":
            if self._allowed is not None:
                try:
                    _ast_security_check(code, self._allowed)
                except PermissionError as exc:
                    return json.dumps({"error": str(exc)})
            argv = [sys.executable, "-c", code]
        elif self._language == "javascript":
            node_bin = shutil.which("node")
            if not node_bin:
                raise RuntimeError("AI Code Execution Tool: Node.js is not available on this host.")
            argv = [node_bin, "-e", code]
        else:
            return json.dumps({"error": f"Unsupported language: {self._language}"})

        workdir = tempfile.mkdtemp(prefix="noodle_code_")
        popen_kwargs: dict[str, Any] = dict(
            cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True  # own process group for killpg
        try:
            proc = subprocess.Popen(argv, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                proc.communicate()
                return json.dumps({"error": f"Code timed out after {timeout}s"})
            truncated = False
            if len(stdout) > self._max_output:
                stdout, truncated = stdout[: self._max_output], True
            if len(stderr) > self._max_output:
                stderr, truncated = stderr[: self._max_output], True
            return json.dumps({
                "stdout": stdout, "stderr": stderr,
                "exit_code": proc.returncode, "truncated": truncated,
            })
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


@node(
    name="AI Code Execution Tool",
    id="ai_code_execution_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    param_groups={"Options": ["allowed_modules", "max_output_chars"]},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "language": {"choices": ["python", "javascript"], "description": "Runtime."},
        "timeout_seconds": {"description": "Hard kill timeout (1-300)."},
        "allowed_modules": {
            "description": "Comma-separated import allowlist (e.g. math,json). Empty = no restriction; list modules to enforce an allowlist.",
            "group": "Options",
        },
        "max_output_chars": {"description": "Truncate stdout/stderr above this.", "group": "Options"},
    },
)
def ai_code_execution_tool(
    name: str = "run_code",
    description: str = "Run Python code and return stdout.",
    language: str = "python",
    timeout_seconds: int = 30,
    allowed_modules: str = "",
    max_output_chars: int = 8000,
) -> ToolAdapter:
    """Supply a sandboxed code-execution tool to a downstream AI Agent."""
    return CodeExecToolAdapter(
        name=name, description=description, language=language,
        allowed_modules=allowed_modules, timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )


# ---------------------------------------------------------------------------
# Web Search Tool
# ---------------------------------------------------------------------------

_SEARCH_PROVIDERS_NEEDING_KEY = {"tavily", "serpapi", "brave"}


class WebSearchToolAdapter(ToolAdapter):
    """Calls a web-search provider API; returns structured results."""

    def __init__(self, *, provider: str, credentials: Any, name: str, description: str,
                 max_results: int, search_depth: str, include_content: bool,
                 timeout_seconds: int) -> None:
        self._provider = (provider or "tavily").lower()
        creds = credentials if isinstance(credentials, dict) else {}
        self._api_key = str(creds.get("api_key") or "").strip()
        if self._provider in _SEARCH_PROVIDERS_NEEDING_KEY and not self._api_key:
            raise ValueError(f"AI Web Search Tool: {self._provider} requires an api_key credential.")
        self._name = name or "web_search"
        self._description = description or "Search the web for current information."
        self._max_results = max(1, min(20, int(max_results or 5)))
        self._search_depth = search_depth or "basic"
        self._include_content = bool(include_content)
        self._timeout = max(1, min(120, int(timeout_seconds or 15)))

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=ToolParameterSchema(
                properties={
                    "query": {"type": "string", "description": "Search query."},
                    "max_results": {"type": "integer", "description": "Optional result count."},
                },
                required=["query"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return False

    def invoke(self, arguments: dict[str, Any]) -> str:
        query = str((arguments or {}).get("query") or "").strip()
        if not query:
            return json.dumps({"results": [], "total": 0, "provider": self._provider})
        limit = self._max_results
        req = arguments.get("max_results")
        if isinstance(req, (int, float)) and 0 < int(req) <= 20:
            limit = int(req)
        try:
            if self._provider == "tavily":
                results = self._tavily(query, limit)
            elif self._provider == "serpapi":
                results = self._serpapi(query, limit)
            elif self._provider == "brave":
                results = self._brave(query, limit)
            else:
                results = self._duckduckgo(query, limit)
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"Search failed: {exc}"})
        if self._include_content and results:
            results[0]["content"] = self._fetch_content(results[0].get("url", ""))
        return json.dumps({"results": results, "total": len(results), "provider": self._provider})

    def _normalise(self, *, title: str, url: str, snippet: str, score: float | None) -> dict[str, Any]:
        return {
            "title": html.unescape(str(title or ""))[:300],
            "url": str(url or ""),
            "snippet": html.unescape(str(snippet or ""))[:500],
            "score": float(score) if score is not None else 0.0,
        }

    def _tavily(self, query: str, limit: int) -> list[dict[str, Any]]:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self._api_key, "query": query,
                  "max_results": limit, "search_depth": self._search_depth},
            timeout=self._timeout,
        )
        if resp.status_code == 429:
            raise httpx.HTTPError("Rate limited. Try again later.")
        if resp.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {resp.status_code}")
        data = resp.json()
        return [self._normalise(title=r.get("title"), url=r.get("url"),
                                snippet=r.get("content"), score=r.get("score"))
                for r in (data.get("results") or [])][:limit]

    def _serpapi(self, query: str, limit: int) -> list[dict[str, Any]]:
        resp = httpx.get("https://serpapi.com/search",
                         params={"q": query, "api_key": self._api_key, "num": limit},
                         timeout=self._timeout)
        if resp.status_code == 429:
            raise httpx.HTTPError("Rate limited. Try again later.")
        if resp.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {resp.status_code}")
        data = resp.json()
        return [self._normalise(title=r.get("title"), url=r.get("link"),
                                snippet=r.get("snippet"), score=None)
                for r in (data.get("organic_results") or [])][:limit]

    def _brave(self, query: str, limit: int) -> list[dict[str, Any]]:
        resp = httpx.get("https://api.search.brave.com/res/v1/web/search",
                         params={"q": query, "count": limit},
                         headers={"X-Subscription-Token": self._api_key},
                         timeout=self._timeout)
        if resp.status_code == 429:
            raise httpx.HTTPError("Rate limited. Try again later.")
        if resp.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {resp.status_code}")
        data = resp.json()
        items = ((data.get("web") or {}).get("results")) or []
        return [self._normalise(title=r.get("title"), url=r.get("url"),
                                snippet=r.get("description"), score=None)
                for r in items][:limit]

    def _duckduckgo(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            import re
            resp = httpx.get("https://duckduckgo.com/html/",
                             params={"q": query}, timeout=self._timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code >= 400:
                return []
            pattern = re.compile(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
            results: list[dict[str, Any]] = []
            for m in pattern.finditer(resp.text):
                url, title_html = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                results.append(self._normalise(title=title_html, url=url, snippet="", score=None))
                if len(results) >= limit:
                    break
            return results
        except (httpx.HTTPError, ValueError):
            return []

    def _fetch_content(self, url: str) -> str:
        if not url:
            return ""
        try:
            resp = safe_request("GET", url, context=f"{self._name} web search content", timeout=self._timeout)
            return resp.text[:8000]
        except Exception:  # noqa: BLE001 - content fetch is best-effort
            return ""


@node(
    name="AI Web Search Tool",
    id="ai_web_search_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    param_groups={"Options": ["search_depth", "include_content", "timeout_seconds"]},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "provider": {"choices": ["tavily", "serpapi", "brave", "duckduckgo"], "description": "Search provider."},
        "credentials": {
            "type": "search_api_key", "label": "Search API key", "multi": True,
            "fields": ["api_key"],
            "description": "API key for the provider (not needed for duckduckgo).",
        },
        "max_results": {"description": "Max results (1-20)."},
        "search_depth": {"choices": ["basic", "advanced"], "description": "Tavily depth.", "group": "Options"},
        "include_content": {"widget": "toggle", "description": "Fetch full content for top result.", "group": "Options"},
        "timeout_seconds": {"description": "Per-request timeout.", "group": "Options"},
    },
)
def ai_web_search_tool(
    name: str = "web_search",
    description: str = "Search the web for current information.",
    provider: str = "tavily",
    credentials: Any = None,
    max_results: int = 5,
    search_depth: str = "basic",
    include_content: bool = False,
    timeout_seconds: int = 15,
) -> ToolAdapter:
    """Supply a web-search tool to a downstream AI Agent."""
    return WebSearchToolAdapter(
        provider=provider, credentials=credentials, name=name, description=description,
        max_results=max_results, search_depth=search_depth,
        include_content=include_content, timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Browser Tool
# ---------------------------------------------------------------------------

_BROWSER_READ_ACTIONS = {"navigate", "extract", "get_links", "screenshot"}
_BROWSER_WRITE_ACTIONS = {"fill_and_submit"}


class BrowserToolAdapter(ToolAdapter):
    """Playwright-backed browser tool. Async-only (invoke_async)."""

    def __init__(self, *, name: str, description: str, allowed_actions: str,
                 wait_strategy: str, timeout_seconds: int, max_content_chars: int) -> None:
        self._name = name or "browse_web"
        self._description = description or "Navigate and extract content from web pages."
        self._allowed = {a.strip() for a in str(allowed_actions or "").split(",") if a.strip()}
        self._wait = wait_strategy if wait_strategy in {"load", "networkidle", "domcontentloaded"} else "load"
        self._timeout_ms = max(1, min(120, int(timeout_seconds or 30))) * 1000
        self._max_chars = max(500, int(max_content_chars or 20000))

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=ToolParameterSchema(
                properties={
                    "action": {"type": "string", "description": f"One of: {sorted(self._allowed)}"},
                    "url": {"type": "string", "description": "Target URL."},
                    "selector": {"type": "string", "description": "Optional CSS selector for extract."},
                },
                required=["action", "url"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return bool(self._allowed & _BROWSER_WRITE_ACTIONS)

    def invoke(self, arguments: dict[str, Any]) -> str:
        raise RuntimeError(f"{self._name}: browser tool is async-only (invoke_async)")

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        action = str((arguments or {}).get("action") or "navigate")
        url = str((arguments or {}).get("url") or "").strip()
        if action not in self._allowed:
            return json.dumps({"error": f"Action '{action}' is not allowed. Allowed: {sorted(self._allowed)}"})
        if not url:
            return json.dumps({"error": "url is required"})
        try:
            assert_public_http_url(url, context=f"{self._name} browser navigation")
        except Exception as exc:  # noqa: BLE001 - SSRF guard raises provider-specific errors
            return json.dumps({"error": f"Blocked URL: {exc}"})
        try:
            from playwright.async_api import async_playwright
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import TimeoutError as PlaywrightTimeout
        except ImportError as exc:
            raise RuntimeError(
                "AI Browser Tool requires playwright. Add playwright>=1.40 to the "
                "environment and run 'playwright install chromium'."
            ) from exc
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(url, wait_until=self._wait, timeout=self._timeout_ms)
                    final_url = page.url
                    assert_public_http_url(final_url, context=f"{self._name} post-redirect")
                    return await self._dispatch(action, page, arguments)
                finally:
                    await browser.close()
        except PlaywrightTimeout:
            return json.dumps({"error": f"Page timed out after {self._timeout_ms // 1000}s", "url": url})
        except PlaywrightError as exc:
            message = str(exc)
            if "Executable doesn't exist" in message:
                return json.dumps({"error": "Browser not installed. Run: playwright install chromium"})
            return json.dumps({"error": f"Browser error: {message}"})

    async def _dispatch(self, action: str, page: Any, arguments: dict[str, Any]) -> str:
        title = await page.title()
        if action in {"navigate", "extract"}:
            selector = str(arguments.get("selector") or "").strip()
            if selector:
                el = await page.query_selector(selector)
                if el is None:
                    return json.dumps({"error": f"Selector '{selector}' not found", "url": page.url})
                text = await el.inner_text()
            else:
                text = await page.inner_text("body")
            truncated = len(text) > self._max_chars
            return json.dumps({"action": action, "url": page.url, "title": title,
                               "content": text[: self._max_chars], "truncated": truncated})
        if action == "get_links":
            hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            return json.dumps({"action": action, "url": page.url, "title": title, "links": hrefs[:200]})
        return json.dumps({"error": f"Action '{action}' not implemented"})


@node(
    name="AI Browser Tool",
    id="ai_browser_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    requirements=["playwright>=1.40"],
    param_groups={"Options": ["wait_strategy", "max_content_chars"]},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "allowed_actions": {"description": "Comma-separated: navigate, extract, get_links, screenshot."},
        "wait_strategy": {"choices": ["load", "networkidle", "domcontentloaded"],
                          "description": "When the page is considered ready.", "group": "Options"},
        "timeout_seconds": {"description": "Page load timeout (1-120)."},
        "max_content_chars": {"description": "Truncate extracted text above this.", "group": "Options"},
    },
)
def ai_browser_tool(
    name: str = "browse_web",
    description: str = "Navigate and extract content from web pages.",
    allowed_actions: str = "navigate,extract,get_links",
    wait_strategy: str = "load",
    timeout_seconds: int = 30,
    max_content_chars: int = 20000,
) -> ToolAdapter:
    """Supply a headless-browser tool to a downstream AI Agent."""
    return BrowserToolAdapter(
        name=name, description=description, allowed_actions=allowed_actions,
        wait_strategy=wait_strategy, timeout_seconds=timeout_seconds,
        max_content_chars=max_content_chars,
    )
