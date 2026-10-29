"""Source-only health boundary checks; never import the application or start probes.

These assert route/auth wiring, not live credential validation. Existing admin
security tests cover verify_admin itself. No server or external services needed.
"""
import ast
from pathlib import Path
import unittest


MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


class TestHealthBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        cls.functions = {
            node.name: node for node in cls.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        }

    def route(self, path):
        matches = []
        for node in self.functions.values():
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "app"
                        and dec.func.attr == "get"
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)
                        and dec.args[0].value == path):
                    matches.append(node)
        self.assertEqual(len(matches), 1, f"Expected one GET {path}")
        return matches[0]

    def test_public_health_minimal_and_has_no_probe_calls(self):
        node = self.route("/health")
        body = node.body[1:] if ast.get_docstring(node) else node.body
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], ast.Return)
        value = body[0].value
        self.assertIsInstance(value, ast.Dict)
        self.assertEqual([ast.literal_eval(k) for k in value.keys],
                         ["status", "app_version"])
        self.assertEqual(ast.literal_eval(value.values[0]), "ok")
        self.assertEqual(ast.dump(value.values[1]), ast.dump(ast.Name(id="APP_VERSION", ctx=ast.Load())))
        self.assertFalse(any(isinstance(n, (ast.Call, ast.Import, ast.ImportFrom, ast.Await))
                             for stmt in node.body for n in ast.walk(stmt)))

    def test_admin_route_requires_real_verify_admin_dependency(self):
        node = self.route("/api/v1/admin/health")
        self.assertEqual(len(node.args.defaults), 1)
        self.assertEqual(ast.unparse(node.args.defaults[0]), "Depends(verify_admin)")
        # Guard must resolve to the existing auth implementation, not a stub.
        imports = [n for n in self.tree.body if isinstance(n, ast.ImportFrom)]
        self.assertTrue(any(n.module == "app.api.admin" and any(
            a.name == "verify_admin" and a.asname is None for a in n.names
        ) for n in imports))
        self.assertTrue(any(n.module == "fastapi" and any(
            a.name == "Depends" and a.asname is None for a in n.names
        ) for n in imports))

    def test_authenticated_admin_reaches_details_only_through_guarded_wrapper(self):
        node = self.route("/api/v1/admin/health")
        self.assertEqual(ast.unparse(node.body[-1]), "return await _detailed_health()")
        callers = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "_detailed_health"]
        self.assertEqual(len(callers), 1)
        helper = self.functions["_detailed_health"]
        self.assertEqual(helper.decorator_list, [])  # never a second public route

    def test_unauthenticated_admin_is_denied_by_existing_guard(self):
        # Source contract, not execution of the auth module (which loads stores).
        admin = ast.parse((MAIN.parent / "api" / "admin.py").read_text(encoding="utf-8"))
        guard = next(n for n in admin.body if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "verify_admin")
        # The fall-through with neither credential ends in 401, never details.
        final = guard.body[-1]
        self.assertIsInstance(final, ast.Raise)
        self.assertEqual(ast.unparse(final.exc),
                         "HTTPException(status_code=401, detail='Unauthorized')")
        # Bearer sessions only succeed after the actual session validator.
        bearer = next(n for n in guard.body if isinstance(n, ast.If)
                      and "_session_is_valid(r, bearer.credentials)" in ast.unparse(n.test))
        self.assertIsInstance(bearer.body[0], ast.Return)

    def test_admin_details_keep_diagnostics(self):
        node = self.functions["_detailed_health"]
        result = node.body[-1].value
        self.assertIsInstance(result, ast.Dict)
        self.assertEqual([ast.literal_eval(k) for k in result.keys], [
            "status", "app_version", "disk", "redis", "celery", "jobs",
            "youtube_circuit_breaker", "ytdlp_version", "js_runtimes", "worker_watchdog",
        ])
        source = ast.unparse(node)
        for probe in ("shutil.disk_usage", "rc.ping()", "_get_active_workers()",
                      "health:db_job_counts", "_ytg.circuit_state()",
                      "importlib.metadata.version", "_js_runtimes_status()", "_watchdog_status()"):
            self.assertIn(probe, source)

    def test_public_api_info_advertises_only_liveness(self):
        node = self.functions["api_info"]
        entries = [n for n in ast.walk(node) if isinstance(n, ast.Dict)
                   and any(isinstance(v, ast.Constant) and v.value == "/health"
                           for v in n.values)]
        self.assertEqual(len(entries), 1)
        entry = ast.literal_eval(entries[0])
        self.assertEqual(entry["description"], "Liveness check: status and app version")


if __name__ == "__main__":
    unittest.main()
