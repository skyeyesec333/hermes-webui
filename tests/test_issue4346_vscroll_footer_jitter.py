"""Static source and behavioral assertions for issue #4346 DOM node recycling."""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
JS = (ROOT / 'static' / 'ui.js').read_text(encoding='utf-8')
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def test_css_vscroll_measuring_guard():
    """style.css suppresses opacity transitions on .msg-foot and .msg-actions
    while .vscroll-measuring is present on the scroll container."""
    assert 'vscroll-measuring' in CSS
    guard_match = re.search(
        r'(?m)^\.vscroll-measuring\s+\.msg-foot,\n'
        r'^\.vscroll-measuring\s+\.msg-actions,\n'
        r'^\.vscroll-measuring\s+\.msg-time\{transition:none !important;\}$',
        CSS,
    )
    assert guard_match, \
        "missing contiguous .vscroll-measuring transition:none !important guard block"


def _run_node(source: str) -> str:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cjs", encoding="utf-8", dir=ROOT, delete=False
    ) as script:
        script.write(source)
        script_path = pathlib.Path(script.name)
    try:
        result = subprocess.run(
            [NODE, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        script_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def _extract_func_script(js: str) -> str:
    return f"""
const src = {js!r};
function extractFunc(name) {{
  const re = new RegExp('function\\\\s+' + name + '\\\\s*\\\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {{
    if (src[i] === '{{') depth++;
    else if (src[i] === '}}') depth--;
    i++;
  }}
  return src.slice(start, i);
}}
"""


def test_js_recycle_flag_exists():
    """ui.js declares the _msgNodeRecycleEnabled flag."""
    assert '_msgNodeRecycleEnabled' in JS


def test_js_recycle_stash_exists():
    """ui.js declares the _recycleStash Map."""
    assert '_recycleStash' in JS


def test_js_recycle_flag_set_in_virtual_render():
    """_scheduleMessageVirtualizedRender sets _msgNodeRecycleEnabled=true
    before the compensate call and clears it in finally."""
    fn_match = re.search(
        r'function _scheduleMessageVirtualizedRender\(force\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "_scheduleMessageVirtualizedRender not found"
    body = fn_match.group(1)
    assert '_msgNodeRecycleEnabled=true' in body
    finally_match = re.search(r'finally\{([^}]*)\}', body)
    assert finally_match, "no finally block in _scheduleMessageVirtualizedRender"
    assert '_msgNodeRecycleEnabled=false' in finally_match.group(1)


def test_js_stash_populated_before_wipe():
    """The recycleStash population loop appears before innerHTML='' in renderMessages."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    stash_pos = body.find('_recycleStash.set(')
    wipe_pos = body.find("inner.innerHTML='';")
    assert stash_pos != -1, "_recycleStash.set not found in renderMessages"
    assert wipe_pos != -1, "innerHTML wipe not found in renderMessages"
    assert stash_pos < wipe_pos, "_recycleStash.set must appear before innerHTML=''"


def test_js_user_row_checks_stash():
    """The user-row creation block checks _recycleStash before createElement."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    assert '_recycleStash.get(rawIdx)' in body, \
        "user row block must check _recycleStash.get(rawIdx)"


def test_assistant_turn_uses_recycle_key_not_msg_idx():
    """Assistant turns use data-recycle-key (not data-msg-idx) to avoid
    colliding with _measureMessageVirtualRow's querySelector."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    assert 'dataset.recycleKey=rawIdx' in body, \
        "assistant turn must use dataset.recycleKey, not dataset.msgIdx"
    assert 'currentAssistantTurn.dataset.msgIdx=rawIdx' not in body, \
        "assistant turn must NOT stamp data-msg-idx (collides with measurement selector)"


def test_recycle_stash_reads_recycle_key():
    """The stash population loop reads data-recycle-key as well as data-msg-idx."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    assert 'dataset.recycleKey' in body, \
        "stash population must read dataset.recycleKey for assistant turns"


def test_recycle_type_check_user_row():
    """The user-row recycling branch type-checks via classList.contains('msg-row')."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    assert "classList.contains('msg-row')" in body, \
        "user-row recycle must type-check with classList.contains('msg-row')"


def test_recycle_type_check_assistant_turn():
    """The assistant-turn recycling branch type-checks via classList.contains('assistant-turn')."""
    fn_match = re.search(
        r'function renderMessages\(options\)\{(.+?)^(?=function )',
        JS, re.DOTALL | re.MULTILINE
    )
    assert fn_match, "renderMessages not found"
    body = fn_match.group(1)
    assert "classList.contains('assistant-turn')" in body, \
        "assistant-turn recycle must type-check with classList.contains('assistant-turn')"


def test_deferred_clear_programmatic_scroll_helper():
    """The _deferClearProgrammaticScroll helper exists and uses debounced timeout."""
    assert '_deferClearProgrammaticScroll' in JS
    assert 'clearTimeout(_programmaticScrollResetTimer)' in JS


def test_no_raf_settimeout_clear_pattern():
    """No rAF→setTimeout chains remain for _programmaticScroll clearing."""
    assert 'requestAnimationFrame(()=>{ setTimeout(()=>{_programmaticScroll=false;},0); })' not in JS, \
        "stale rAF→setTimeout clear pattern found; should use _deferClearProgrammaticScroll"
    assert 'requestAnimationFrame(()=>{ _programmaticScroll=false; })' not in JS, \
        "stale rAF clear pattern found; should use _deferClearProgrammaticScroll"


def test_measurement_selector_does_not_match_assistant_turn():
    """_measureMessageVirtualRow's querySelector('[data-msg-idx=...]') must not
    match .assistant-turn containers, only .assistant-segment or .msg-row."""
    js = JS
    source = _extract_func_script(js) + """
eval(extractFunc('_measureMessageVirtualRow'));
// Build a DOM-like tree: .assistant-turn (with data-recycle-key only)
//   └── .assistant-segment (with data-msg-idx="5")
//         sibling → tool-card-row (60px)
//         sibling → next .assistant-segment (data-msg-idx="6")
const seg5 = {
  classList: { contains(name){ return name === 'assistant-segment'; } },
  getBoundingClientRect(){ return {height: 120}; },
  nextElementSibling: {
    hasAttribute(){ return false; },
    matches(sel){ return sel.indexOf('tool-card-row') >= 0; },
    getBoundingClientRect(){ return {height: 60}; },
    nextElementSibling: {
      hasAttribute(name){ return name === 'data-msg-idx'; },
      getBoundingClientRect(){ return {height: 999}; },
      nextElementSibling: null,
    },
  },
};
const turn = {
  // .assistant-turn has data-recycle-key but NOT data-msg-idx
  dataset: { recycleKey: '5' },
  classList: { contains(name){ return name === 'assistant-turn'; } },
  querySelector(sel){ return null; },
};
const inner = {
  querySelector(selector){
    // data-msg-idx="5" should resolve to the segment, not the turn
    if(selector === '[data-msg-idx="5"]') return seg5;
    return null;
  },
};
const height = _measureMessageVirtualRow(inner, {rawIdx: 5});
// segment (120) + tool-card sibling (60) = 180, NOT the turn container height
console.log(JSON.stringify({height, correct: height === 180}));
"""
    metrics = json.loads(_run_node(source))
    assert metrics["correct"] is True, (
        f"expected 180 (segment + tool sibling), got {metrics['height']}"
    )
