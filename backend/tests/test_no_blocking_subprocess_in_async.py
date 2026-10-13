"""13/10/2026: /trim, /to-gif, … ran ffmpeg with subprocess.run directly
inside `async def` handlers — each call froze the whole uvicorn process
(/health included) for up to 5 minutes. Blocking calls in async API handlers
must go through asyncio.to_thread (or a sync `def` handler)."""
import ast
import pathlib

BLOCKING = {"run", "call", "check_output", "check_call", "Popen"}
API = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"


def _direct_blocking_calls(fn):
    hits = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in BLOCKING and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in ("subprocess", "_subprocess")):
                hits.append(child.lineno)
            visit(child)
    visit(fn)
    return hits


def test_async_api_handlers_never_call_subprocess_directly():
    offenders = []
    for path in sorted(API.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.AsyncFunctionDef):
                for line in _direct_blocking_calls(fn):
                    offenders.append(f"{path.name}:{line} in async {fn.name}")
    assert offenders == [], "blocking subprocess in async handler:\n" + "\n".join(offenders)
