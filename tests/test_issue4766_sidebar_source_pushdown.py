"""Regression coverage for issue #4766: `/api/sessions` filters by active sidebar source."""

import io
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

import api.profiles as profiles
import api.routes as routes
import pytest


ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = ROOT / "static" / "sessions.js"
NODE = shutil.which("node")


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _session_rows(
    webui_count,
    cli_count,
    archived_webui_count=0,
    archived_cli_count=0,
    start=0,
):
    rows = []
    for index in range(webui_count):
        rows.append(
            {
                "session_id": f"webui-{start + index}",
                "title": "WebUI Session",
                "profile": "default",
                "archived": index < archived_webui_count,
                "message_count": 1,
                "updated_at": 1000 + index,
                "last_message_at": 1000 + index,
                "source": "webui",
                "raw_source": "webui",
                "session_source": "webui",
                "source_tag": "webui",
            }
        )
    for index in range(cli_count):
        rows.append(
            {
                "session_id": f"cli-{start + index + 10000}",
                "title": "Imported CLI session",
                "profile": "default",
                "archived": index < archived_cli_count,
                "message_count": 1,
                "updated_at": 2000 + index,
                "last_message_at": 2000 + index,
                "source": "cli",
                "raw_source": "cli",
                "session_source": "cli",
                "source_tag": "cli",
            }
        )
    return rows


def _handle_sessions(url):
    handler = _FakeHandler()
    routes.handle_get(handler, urlparse(url))
    return handler


def _extract_function(source_text, function_name):
    marker = f"function {function_name}("
    start = source_text.index(marker)
    brace_start = source_text.index("{", start)
    depth = 0
    for index in range(brace_start, len(source_text)):
        char = source_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source_text[start : index + 1]
    raise AssertionError(f"Could not extract {function_name}")


def _run_node(script):
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


@pytest.fixture(autouse=True)
def _clear_cache():
    routes._session_list_cache_clear()
    yield
    routes._session_list_cache_clear()


def _install_common_monkeypatches(monkeypatch, rows):
    enriched = []
    row_ids = {str(row["session_id"]) for row in rows if row.get("session_id")}
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: list(rows))
    monkeypatch.setattr(routes, "_reconcile_stale_stream_state_for_session_rows", lambda _rows: False)
    monkeypatch.setattr(routes, "_enrich_sidebar_lineage_metadata", lambda rows: enriched.append([r["session_id"] for r in rows]))
    monkeypatch.setattr(routes, "get_cli_sessions", lambda source_filter=None, all_profiles=False: [])
    monkeypatch.setattr(routes, "agent_session_rows_existing", lambda ids, profile=None: set(row_ids & {str(sid) for sid in ids}))
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": True})
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    return enriched


def test_sidebar_source_webui_excludes_cli_rows(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20)
    enriched = _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions?sidebar_source=webui")

    body = handler.json_body()
    assert handler.status == 200
    assert len(body["sessions"]) == 30
    assert all(r["session_id"].startswith("webui-") for r in body["sessions"])
    assert body["webui_session_count"] == 30
    assert body["cli_session_count"] == 20
    assert body["archived_count"] == 0
    expected = {
        row["session_id"] for row in rows
        if not row["archived"] and row["session_id"].startswith("webui-")
    }
    assert set(enriched[0]) == expected


def test_sidebar_source_cli_excludes_webui_rows(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20)
    _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions?sidebar_source=cli")

    body = handler.json_body()
    assert handler.status == 200
    assert len(body["sessions"]) == 20
    assert all(r["session_id"].startswith("cli-") for r in body["sessions"])
    assert body["webui_session_count"] == 30
    assert body["cli_session_count"] == 20


def test_sidebar_source_omitted_returns_all_rows(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20)
    _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions")

    body = handler.json_body()
    assert handler.status == 200
    assert len(body["sessions"]) == 50
    assert len([r for r in body["sessions"] if r["session_id"].startswith("webui-")]) == 30
    assert len([r for r in body["sessions"] if r["session_id"].startswith("cli-")]) == 20


def test_sidebar_source_returns_cross_bucket_counts(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20, archived_webui_count=2, archived_cli_count=3)
    _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions?sidebar_source=webui&include_archived=1")
    webui_rows = [r for r in rows if r["session_id"].startswith("webui-")]
    cli_rows = [r for r in rows if r["session_id"].startswith("cli-")]

    body = handler.json_body()
    assert handler.status == 200
    assert body["webui_session_count"] == len(webui_rows)
    assert body["cli_session_count"] == len(cli_rows)


def test_sidebar_source_preserves_archived_counts(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20, archived_webui_count=2, archived_cli_count=3)
    _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions?sidebar_source=webui&include_archived=1")
    body = handler.json_body()

    assert handler.status == 200
    assert body["archived_webui_count"] == 2
    assert body["archived_cli_count"] == 3
    assert body["archived_count"] == 5
    assert len([r for r in body["sessions"] if r["archived"]]) == 2


def test_sidebar_source_varies_cache_key():
    key_webui = routes._session_list_cache_key(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
        include_archived=False,
        sidebar_source="webui",
    )
    key_cli = routes._session_list_cache_key(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
        include_archived=False,
        sidebar_source="cli",
    )
    key_omitted = routes._session_list_cache_key(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
        include_archived=False,
        sidebar_source=None,
    )

    assert key_webui != key_cli
    assert key_webui != key_omitted
    assert key_cli != key_omitted


def test_frontend_sends_sidebar_source_param():
    src = SESSIONS_JS.read_text(encoding="utf-8")

    assert "function _sessionListQueryString()" in src
    assert "qs.set('sidebar_source', requestSidebarSource);" in src
    assert "_serverWebuiSessionCount" in src
    assert "_serverCliSessionCount" in src
    assert "function _sessionSourceTabCount(" in src


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_session_list_query_string_respects_sidebar_source_and_flags():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    query_fn = _extract_function(src, "_sessionListQueryString")
    script = f"""
global.window = {{ _showCliSessions: true }};
global._sessionSourceFilter = 'cli';
global._showAllProfiles = true;
global._showArchived = false;
{query_fn}
const first = _sessionListQueryString();
window._showCliSessions = false;
global._showArchived = true;
const second = _sessionListQueryString();
console.log(JSON.stringify({{ first, second }}));
"""
    body = _run_node(script)

    assert body["first"] == "?sidebar_source=cli&all_profiles=1"
    assert body["second"] == "?sidebar_source=webui&all_profiles=1&include_archived=1"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_session_source_switch_fetches_selected_bucket():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    fn_source = _extract_function(src, "_setSessionSourceFilter")
    script = f"""
const renderCalls = [];
global._sessionSourceFilter = 'webui';
global._activeProject = 'demo-project';
global._selectedSessions = new Set(['first', 'second']);
global._sessionSelectMode = true;
global.localStorage = {{
  writes: [],
  setItem(key, value) {{
    this.writes.push([key, value]);
  }},
}};
global.renderSessionList = (opts) => {{
  renderCalls.push(opts);
  return Promise.resolve();
}};
{fn_source}
_setSessionSourceFilter('cli');
console.log(JSON.stringify({{
  sourceFilter: global._sessionSourceFilter,
  activeProject: global._activeProject,
  selectedSize: global._selectedSessions.size,
  sessionSelectMode: global._sessionSelectMode,
  storageWrites: global.localStorage.writes,
  renderCalls,
}}));
"""
    body = _run_node(script)

    assert body["sourceFilter"] == "cli"
    assert body["activeProject"] is None
    assert body["selectedSize"] == 0
    assert body["sessionSelectMode"] is False
    assert body["storageWrites"] == [["hermes-session-source-filter", "cli"]]
    assert body["renderCalls"] == [{"deferWhileInteracting": False}]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_apply_payload_and_tab_count_helpers_cover_old_and_new_payloads():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    apply_fn = _extract_function(src, "_applySessionListPayload")
    count_fn = _extract_function(src, "_sessionSourceTabCount")
    clear_fn = _extract_function(src, "_clearSessionSourceTabCounts")
    script = f"""
global._otherProfileCount = 0;
global._archivedWebuiCount = 0;
global._archivedCliCount = 0;
global._serverWebuiSessionCount = null;
global._serverCliSessionCount = null;
global._serverTimeDelta = 0;
global._serverTz = null;
global._optimisticallyRemovedSessionIds = new Set();
global._allSessions = [];
global._allSessionsScope = null;
global._allProjects = [];
global._sessionListLoadError = null;
global._sessionListHasLoadedOnce = false;
global._sessionListFirstRenderAnimated = true;
global._sessionListSkeletonActive = true;
global._showAllProfiles = false;
global.S = {{ activeProfile: 'default' }};
global._reconcileActiveSessionIdleStateFromList = rows => rows;
global._mergeOptimisticFirstTurnSessions = rows => rows;
global._syncSessionAttentionSoundState = () => {{}};
global._pruneLineageReportCacheToVisibleSessions = () => {{}};
global._markPollingCompletionUnreadTransitions = () => {{}};
global._isSessionEffectivelyStreaming = () => false;
global.startStreamingPoll = () => {{}};
global.stopStreamingPoll = () => {{}};
global.ensureSessionTimeRefreshPoll = () => {{}};
global.ensureActiveSessionExternalRefreshPoll = () => {{}};
global.ensureSessionEventsSSE = () => {{}};
global.animateNextSessionListRefresh = () => {{}};
global.renderSessionListFromCache = () => {{}};
{clear_fn}
{count_fn}
{apply_fn}
const sessions = [{{ session_id: 'webui-1' }}];
_applySessionListPayload({{ sessions, other_profile_count: 0, archived_count: 0, active_profile: 'default' }}, {{ projects: [] }});
const oldPayload = {{
  webui: _sessionSourceTabCount('webui', 7, 3),
  cli: _sessionSourceTabCount('cli', 7, 3),
}};
_clearSessionSourceTabCounts();
_applySessionListPayload({{
  sessions,
  other_profile_count: 0,
  archived_count: 0,
  active_profile: 'default',
  webui_session_count: 11,
  cli_session_count: 5,
}}, {{ projects: [] }});
const newPayload = {{
  webui: _sessionSourceTabCount('webui', 7, 3),
  cli: _sessionSourceTabCount('cli', 7, 3),
}};
console.log(JSON.stringify({{ oldPayload, newPayload }}));
"""
    body = _run_node(script)

    assert body["oldPayload"] == {"webui": 7, "cli": 3}
    assert body["newPayload"] == {"webui": 11, "cli": 5}


def test_session_list_response_omits_bucket_counts_when_missing(monkeypatch):
    monkeypatch.setattr(routes, "_session_list_cache_overlay_runtime_rows", lambda rows: rows)
    monkeypatch.setattr(routes, "_sidebar_session_response_item", lambda row: row)

    body = routes._session_list_payload_to_response(
        {
            "sessions": [{"session_id": "webui-1", "title": "WebUI Session"}],
            "cli_count": 0,
            "archived_count": 0,
            "archived_webui_count": 0,
            "archived_cli_count": 0,
            "include_archived": False,
            "all_profiles": False,
            "active_profile": "default",
            "other_profile_count": 0,
        }
    )

    assert "webui_session_count" not in body
    assert "cli_session_count" not in body
    assert body["sessions"][0]["session_id"] == "webui-1"


def test_scope_mismatch_error_path_clears_server_tab_counts():
    src = SESSIONS_JS.read_text(encoding="utf-8")

    assert "_clearSessionSourceTabCounts();" in src


def test_payload_row_count_regression(monkeypatch):
    rows = _session_rows(webui_count=30, cli_count=20)
    _install_common_monkeypatches(monkeypatch, rows)

    handler = _handle_sessions("http://example.com/api/sessions?sidebar_source=webui")
    body = handler.json_body()

    assert handler.status == 200
    assert len(body["sessions"]) == 30
