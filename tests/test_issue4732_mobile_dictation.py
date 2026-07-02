"""Regression tests for #4732 / PR #5294 mobile dictation.

The PR keeps mobile dictation alive across natural thinking pauses by making
SpeechRecognition ``continuous`` and auto-restarting on ``onend``. Two gate
requirements protect the established behavior:

1. Continuous + onend auto-restart must be gated to mobile / coarse-pointer
   devices (the SAME matchMedia coarse/fine signal the Enter-key mobile default
   uses). On DESKTOP (a fine pointer present) dictation must stay one-shot —
   ``continuous=false`` with the mic committing + auto-stopping on a pause,
   byte-equivalent to master. It must NOT be unconditional ``sr.continuous=true``.

2. The async Wake Lock acquire must be token-guarded: capturing a
   request-generation token before ``await wakeLock.request('screen')`` and, if
   the token is no longer current (mic was stopped/hidden mid-flight), releasing
   the late lock instead of storing it — otherwise the screen wake lock leaks.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")


def _slice_between(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def test_continuous_is_gated_not_unconditional():
    """sr.continuous must be driven by a mobile/coarse-pointer gate, never a
    hardcoded `sr.continuous=true`."""
    ensure_body = _slice_between(
        BOOT_JS,
        "function _ensureSpeechRecognition(){",
        "\n    return sr;",
    )
    # Regression guard: the raw unconditional assignment introduced by the PR
    # must NOT survive. Desktop must stay one-shot.
    assert "sr.continuous=true" not in ensure_body
    # Continuous comes from a computed gate.
    assert "const _continuous=_speechContinuousEnabled();" in ensure_body
    assert "sr.continuous=_continuous;" in ensure_body


def test_speech_continuous_gate_uses_existing_coarse_pointer_helper():
    """The gate must reuse the established coarse/fine pointer signal
    (matchMedia('(pointer:coarse)') && !_hasFinePointerCoexisting()), the same
    pattern the Enter-key mobile default uses — not a new detection scheme."""
    gate = _slice_between(
        BOOT_JS,
        "function _speechContinuousEnabled(){",
        "\n  }",
    )
    assert "matchMedia('(pointer:coarse)').matches" in gate
    assert "_hasFinePointerCoexisting()" in gate
    # The established helper it depends on must still exist in the file.
    assert "function _hasFinePointerCoexisting(){" in BOOT_JS
    assert "matchMedia('(any-pointer:fine)').matches" in BOOT_JS
    # Optional explicit opt-in is also honored (does not break the other path).
    assert "hermes-voice-continuous" in gate


def test_onend_auto_restart_is_gated_behind_continuous():
    """The onend `sr.start()` auto-restart must only fire when the continuous
    gate is on. On desktop (_continuous=false) the mic commits + stops one-shot."""
    onend_body = _slice_between(
        BOOT_JS,
        "sr.onend=()=>{",
        "\n    };",
    )
    # Restart condition must be gated by _continuous, not just the mic-active check.
    assert "if(_continuous&&!_speechStopRequested&&window._micActive" in onend_body
    assert "sr.start();" in onend_body
    # The one-shot commit/stop path is still present for the desktop branch.
    assert "_setRecording(false);" in onend_body


def test_onerror_pause_swallow_is_gated_behind_continuous():
    """The no-speech/aborted keep-alive swallow (a continuous-mode behavior) must
    also be gated so desktop error handling stays byte-equivalent to master."""
    onerror_body = _slice_between(
        BOOT_JS,
        "sr.onerror=(event)=>{",
        "\n    return sr;",
    )
    assert "if(_continuous" in onerror_body
    assert "event.error==='no-speech'||event.error==='aborted'" in onerror_body


def test_wakelock_acquire_has_generation_token_guard():
    """_acquireMicWakeLock must capture a generation token before the await and,
    after the request resolves, release the late lock (not store it) when the
    token is stale or the mic is no longer actively recording."""
    acquire_body = _slice_between(
        BOOT_JS,
        "async function _acquireMicWakeLock(){",
        "\n  }",
    )
    # Module-level token declared.
    assert "let _micWakeLockToken=0;" in BOOT_JS
    # Token captured BEFORE the await.
    token_idx = acquire_body.index("const token=_micWakeLockToken;")
    await_idx = acquire_body.index("await navigator.wakeLock.request('screen')")
    assert token_idx < await_idx, "token must be captured before the await"
    # Stale-token / not-active guard AFTER the await releases the lock.
    assert "token!==_micWakeLockToken" in acquire_body
    assert "window._micActive&&_activeCaptureMode==='speech'&&document.visibilityState!=='hidden'" in acquire_body
    guard_idx = acquire_body.index("token!==_micWakeLockToken")
    release_in_guard = acquire_body.index("lock.release()", guard_idx)
    store_idx = acquire_body.index("_micWakeLock=lock;")
    assert release_in_guard < store_idx, "stale lock must be released before the store path"
    # Non-fatal: wrapped in try/catch like the existing path.
    assert "}catch(_){" in acquire_body


def test_wakelock_token_bumped_on_every_stop_and_release():
    """The token must be incremented on every stop/release so an in-flight
    acquire is invalidated."""
    release_body = _slice_between(
        BOOT_JS,
        "async function _releaseMicWakeLock(){",
        "\n  }",
    )
    assert "_micWakeLockToken+=1;" in release_body

    stop_body = _slice_between(
        BOOT_JS,
        "function _stopMic(){",
        "\n  }",
    )
    assert "_micWakeLockToken+=1;" in stop_body
