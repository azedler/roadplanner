"""Source-level contract: a slow provider-call action must outlive the
WebSocket connection that started it.

Real bug report: "Assistent konnte nicht antworten - Connection lost" every
time the mobile app was backgrounded mid-chat. assistant_chat/assistant_
prepare/assistant_test/assistant_briefing all make a potentially long
(seconds to over a minute) call to the configured AI provider. Home
Assistant's websocket_api cancels whichever task is awaiting a connection
that just closed - previously that was the same task actually running the
provider call, so backgrounding the app didn't just lose the *reply*, it
aborted the *request* entirely, discarding whatever the model had already
produced. Wrapping these four actions in hass.async_create_task (a task
tracked by Home Assistant core, not by this specific connection) plus
asyncio.shield (which protects the inner task from the outer await's own
cancellation, without swallowing real provider errors) lets the call run to
completion regardless of the connection's fate - the frontend's existing
_assistantFailureResolved check then finds the real answer on next reload
instead of the conversation staying stuck on an unanswered message forever.
"""
from pathlib import Path

PANEL = Path("custom_components/roadplanner_mcp/panel.py").read_text(encoding="utf-8")

assert (
    "_PROVIDER_CALL_ACTIONS = {\n"
    '    "assistant_chat",\n'
    '    "assistant_prepare",\n'
    '    "assistant_test",\n'
    '    "assistant_briefing",\n'
    "}"
) in PANEL

assert "if msg[\"action\"] in _PROVIDER_CALL_ACTIONS:" in PANEL
assert (
    "result = await asyncio.shield(hass.async_create_task(action_call))"
) in PANEL
# The non-provider-call path must still behave exactly as before (no shield,
# no detached task) - only the four slow actions get this treatment.
assert "result = await action_call" in PANEL

print("Provider-call disconnect survival contract tests passed.")
