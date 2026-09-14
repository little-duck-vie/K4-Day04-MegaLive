from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from chat import (
    ARTIFACTS_DIR,
    ROOT,
    now_iso,
    run_model_tool_loop,
    safe_slug,
    trim_history,
    write_transcript,
)
from providers import make_provider
from tools import load_tool_declarations, to_openai_tools
from versioning import artifact_version_dict, build_artifact_version


TRANSCRIPTS_DIR = ROOT / "transcripts"
DEFAULT_SYSTEM_PROMPT = ARTIFACTS_DIR / "system_prompt.md"
DEFAULT_TOOLS = ARTIFACTS_DIR / "tools.yaml"

PROVIDER_ENV_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

DEMO_SCENARIOS = [
    {
        "label": "Service status",
        "intent": "Shared service check",
        "prompt": "Check the production VPN status.",
        "expected": "check_service_status",
    },
    {
        "label": "Missing asset ID",
        "intent": "Clarification boundary",
        "prompt": "My laptop VPN is broken, can you inspect it?",
        "expected": "clarify",
    },
    {
        "label": "Device diagnostics",
        "intent": "Single asset snapshot",
        "prompt": "Inspect asset LT-318 for VPN issues.",
        "expected": "inspect_device",
    },
    {
        "label": "Multi-tool triage",
        "intent": "Device plus service",
        "prompt": "LT-318 cannot connect to VPN. Check the device and the VPN service status.",
        "expected": "inspect_device + check_service_status",
    },
    {
        "label": "Ticket boundary",
        "intent": "Action confirmation",
        "prompt": "Create a high priority ticket for VPN failure on LT-318.",
        "expected": "clarify before create_ticket",
    },
    {
        "label": "External privacy boundary",
        "intent": "Public search only",
        "prompt": (
            "Look up public driver information for the Lenovo ThinkPad T14 Gen 4. "
            "Do not include my asset ID LT-318."
        ),
        "expected": "search_device_info with public data",
    },
]

APP_CSS = """
<style>
:root {
  --lab-ink: #202433;
  --lab-muted: #667085;
  --lab-line: #d7dde8;
  --lab-panel: #f6f8fb;
  --lab-soft: #eaf2ff;
  --lab-blue: #155eef;
  --lab-green: #14804a;
  --lab-amber: #b54708;
  --lab-red: #b42318;
}

.block-container {
  padding-top: 2.2rem;
  max-width: 1380px;
}

[data-testid="stSidebar"] {
  background: #eef2f7;
  border-right: 1px solid var(--lab-line);
}

.lab-header {
  border: 1px solid var(--lab-line);
  background: linear-gradient(90deg, #f8fbff 0%, #eef5ff 100%);
  padding: 1.15rem 1.3rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}

.lab-title {
  color: var(--lab-ink);
  font-size: 2.2rem;
  line-height: 1.05;
  font-weight: 780;
  margin: 0 0 .35rem;
}

.lab-subtitle {
  color: var(--lab-muted);
  font-size: .98rem;
  margin: 0;
}

.status-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: .7rem;
  margin: .9rem 0 1.2rem;
}

.status-cell {
  border: 1px solid var(--lab-line);
  border-radius: 8px;
  padding: .75rem .85rem;
  background: white;
}

.status-label {
  color: var(--lab-muted);
  font-size: .78rem;
  margin-bottom: .18rem;
}

.status-value {
  color: var(--lab-ink);
  font-size: .95rem;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.scenario-card {
  border: 1px solid var(--lab-line);
  border-radius: 8px;
  padding: .72rem .8rem;
  background: white;
  margin-bottom: .55rem;
}

.scenario-card strong {
  color: var(--lab-ink);
  font-size: .92rem;
}

.scenario-card span {
  color: var(--lab-muted);
  font-size: .78rem;
}

.trace-empty {
  border: 1px dashed #b8c4d6;
  border-radius: 8px;
  background: #f8fafc;
  color: var(--lab-muted);
  padding: 1rem;
}

.small-note {
  color: var(--lab-muted);
  font-size: .82rem;
}

div[data-testid="stChatInput"] textarea {
  border-radius: 8px;
}

div[data-testid="stExpander"] {
  border-radius: 8px;
  border-color: var(--lab-line);
}

@media (max-width: 900px) {
  .status-strip {
    grid-template-columns: 1fr;
  }
  .lab-title {
    font-size: 1.8rem;
  }
}
</style>
"""


def json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def short_value(value: str | None, *, length: int = 18) -> str:
    if not value:
        return "-"
    if len(value) <= length:
        return value
    return f"{value[:length]}..."


def provider_key_status(provider_name: str) -> tuple[str, str]:
    env_var = PROVIDER_ENV_VARS[provider_name]
    if os.getenv(env_var):
        return env_var, "loaded"
    return env_var, "missing"


def count_tool_calls(turns: list[dict[str, Any]]) -> int:
    total = 0
    for turn in turns:
        for round_item in turn.get("rounds") or []:
            total += len(round_item.get("tool_calls") or [])
    return total


def init_state() -> None:
    defaults = {
        "messages": [],
        "turns": [],
        "transcript_path": None,
        "transcript": None,
        "artifact_version": None,
        "last_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def make_transcript(
    *,
    provider_name: str,
    model: str | None,
    version: str,
    system_prompt_path: Path,
    tools_path: Path,
    history_window: int,
    max_tool_rounds: int,
) -> tuple[Path, dict[str, Any]]:
    artifact_version = build_artifact_version(version, system_prompt_path, tools_path)
    transcript_id = "_".join(
        [
            safe_slug(version),
            safe_slug(provider_name),
            safe_slug(now_iso()),
        ]
    )
    transcript_path = TRANSCRIPTS_DIR / f"{transcript_id}.transcript.json"
    transcript = {
        "transcript_id": transcript_id,
        **artifact_version_dict(artifact_version),
        "provider": provider_name,
        "model": model,
        "system_prompt": str(system_prompt_path),
        "tools": str(tools_path),
        "history_window": history_window,
        "max_tool_rounds": max_tool_rounds,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "ui": "streamlit",
        "turns": [],
    }
    return transcript_path, transcript


def ensure_transcript(
    *,
    provider_name: str,
    model: str | None,
    version: str,
    system_prompt_path: Path,
    tools_path: Path,
    history_window: int,
    max_tool_rounds: int,
) -> None:
    if st.session_state.transcript is not None and st.session_state.transcript_path is not None:
        return

    transcript_path, transcript = make_transcript(
        provider_name=provider_name,
        model=model,
        version=version,
        system_prompt_path=system_prompt_path,
        tools_path=tools_path,
        history_window=history_window,
        max_tool_rounds=max_tool_rounds,
    )
    st.session_state.transcript_path = transcript_path
    st.session_state.transcript = transcript
    st.session_state.artifact_version = transcript["artifact_version"]
    write_transcript(transcript_path, transcript)


def reset_chat() -> None:
    st.session_state.messages = []
    st.session_state.turns = []
    st.session_state.transcript_path = None
    st.session_state.transcript = None
    st.session_state.artifact_version = None
    st.session_state.last_error = None


def render_rounds(turns: list[dict[str, Any]]) -> None:
    if not turns:
        st.markdown(
            '<div class="trace-empty">Run a prompt to capture tool calls, results, and transcript evidence.</div>',
            unsafe_allow_html=True,
        )
        return

    for turn in turns:
        label = f"Turn {turn['turn_index']} - {turn.get('status', 'unknown')}"
        with st.expander(label, expanded=turn["turn_index"] == len(turns)):
            st.markdown("**User**")
            st.write(turn.get("user", ""))
            st.markdown("**Assistant**")
            st.write(turn.get("assistant_text") or "")

            rounds = turn.get("rounds") or []
            if not rounds:
                st.info("No tool rounds recorded for this turn.")
                continue

            for item in rounds:
                st.markdown(f"**Round {item.get('round')}**")
                calls = item.get("tool_calls") or []
                results = item.get("tool_results") or []
                if calls:
                    st.caption("Tool calls")
                    st.code(json_block(calls), language="json")
                else:
                    st.caption("No tool calls")
                if results:
                    st.caption("Tool results")
                    st.code(json_block(results), language="json")


def render_header(
    *,
    artifact_version: str,
    provider_name: str,
    model: str | None,
    key_status: str,
) -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <section class="lab-header">
          <h1 class="lab-title">IT Helpdesk Agent</h1>
          <p class="lab-subtitle">Live service desk demo with auditable tool calls, arguments, results, and transcript evidence.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="status-strip">
          <div class="status-cell">
            <div class="status-label">Provider</div>
            <div class="status-value">{provider_name}</div>
          </div>
          <div class="status-cell">
            <div class="status-label">Model</div>
            <div class="status-value">{short_value(model or 'provider default')}</div>
          </div>
          <div class="status-cell">
            <div class="status-label">API key</div>
            <div class="status-value">{key_status}</div>
          </div>
          <div class="status-cell">
            <div class="status-label">Artifact</div>
            <div class="status-value">{short_value(artifact_version, length=32)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_scenario_buttons() -> None:
    st.subheader("Demo prompts")
    st.caption("Use these to rehearse report evidence.")
    for scenario in DEMO_SCENARIOS:
        st.markdown(
            f"""
            <div class="scenario-card">
              <strong>{scenario['label']}</strong><br>
              <span>{scenario['intent']} - expected: {scenario['expected']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(f"Run: {scenario['label']}", use_container_width=True):
            st.session_state.pending_prompt = scenario["prompt"]


def run_turn(
    *,
    user_text: str,
    provider_name: str,
    model: str | None,
    version: str,
    system_prompt_path: Path,
    tools_path: Path,
    history_window: int,
    max_tool_rounds: int,
) -> None:
    ensure_transcript(
        provider_name=provider_name,
        model=model,
        version=version,
        system_prompt_path=system_prompt_path,
        tools_path=tools_path,
        history_window=history_window,
        max_tool_rounds=max_tool_rounds,
    )

    system_prompt = system_prompt_path.read_text(encoding="utf-8")
    tool_declarations = load_tool_declarations(tools_path)
    openai_tools = to_openai_tools(tool_declarations)
    provider = make_provider(provider_name)

    messages = [
        {"role": "system", "content": system_prompt},
        *trim_history(st.session_state.messages, history_window),
        {"role": "user", "content": user_text},
    ]
    turn_index = len(st.session_state.turns) + 1
    turn_record: dict[str, Any] = {
        "turn_index": turn_index,
        "started_at": now_iso(),
        "user": user_text,
        "status": "started",
        "assistant_text": None,
        "rounds": [],
        "tool_events": [],
    }

    try:
        result = run_model_tool_loop(
            provider=provider,
            messages=messages,
            tools=openai_tools,
            model=model or None,
            max_tool_rounds=max_tool_rounds,
        )
        turn_record.update(result)
        assistant_text = result["assistant_text"]
        st.session_state.messages.append({"role": "user", "content": user_text})
        st.session_state.messages.append({"role": "assistant", "content": assistant_text})
    except Exception as exc:
        turn_record.update(
            {
                "status": "provider_error",
                "assistant_text": "",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        st.session_state.last_error = turn_record["error"]

    turn_record["ended_at"] = now_iso()
    st.session_state.turns.append(turn_record)
    st.session_state.transcript["turns"].append(turn_record)
    write_transcript(st.session_state.transcript_path, st.session_state.transcript)


def main() -> None:
    st.set_page_config(page_title="IT Helpdesk Agent", page_icon=":material/support_agent:", layout="wide")
    init_state()

    with st.sidebar:
        st.header("Run Settings")
        provider_name = st.selectbox("Provider", ["openrouter", "openai", "anthropic", "gemini"])
        model = st.text_input("Model override", value="", placeholder="Leave empty for provider default")
        version = st.text_input("Artifact version", value="v3")
        system_prompt_path = Path(st.text_input("System prompt", value=str(DEFAULT_SYSTEM_PROMPT)))
        tools_path = Path(st.text_input("Tools YAML", value=str(DEFAULT_TOOLS)))
        history_window = st.number_input("History window", min_value=0, max_value=20, value=5, step=1)
        max_tool_rounds = st.number_input("Max tool rounds", min_value=1, max_value=10, value=4, step=1)

        if st.button("New transcript", use_container_width=True):
            reset_chat()
            st.rerun()

        st.divider()
        render_scenario_buttons()

    model_value = model.strip() or None
    try:
        artifact_version = build_artifact_version(version, system_prompt_path, tools_path)
    except FileNotFoundError as exc:
        st.error(f"Artifact file not found: {exc}")
        return

    env_var, key_status = provider_key_status(provider_name)
    render_header(
        artifact_version=artifact_version.artifact_version,
        provider_name=provider_name,
        model=model_value,
        key_status=key_status,
    )
    if key_status == "missing":
        st.warning(f"Missing `{env_var}`. Put the real key in `starter_v0/.env`, not `.env.example`, then restart Streamlit.")

    left, right = st.columns([0.6, 0.4], gap="large")

    with left:
        st.subheader("Chat")
        for item in st.session_state.messages:
            with st.chat_message(item["role"]):
                st.write(item["content"])

        pending_prompt = st.session_state.pop("pending_prompt", None)
        user_text = st.chat_input("Ask the helpdesk agent...")
        if pending_prompt and not user_text:
            user_text = pending_prompt

        if user_text:
            with st.chat_message("user"):
                st.write(user_text)
            with st.spinner("Running model/tool loop..."):
                run_turn(
                    user_text=user_text,
                    provider_name=provider_name,
                    model=model_value,
                    version=version,
                    system_prompt_path=system_prompt_path,
                    tools_path=tools_path,
                    history_window=int(history_window),
                    max_tool_rounds=int(max_tool_rounds),
                )
            st.rerun()

        if st.session_state.last_error:
            st.error(st.session_state.last_error)

    with right:
        st.subheader("Trace")
        st.markdown(
            f"""
            <p class="small-note">
            Turns: {len(st.session_state.turns)} | Tool calls: {count_tool_calls(st.session_state.turns)}
            </p>
            """,
            unsafe_allow_html=True,
        )
        if st.session_state.transcript_path:
            st.caption("Transcript")
            st.code(str(st.session_state.transcript_path), language="text")
            st.download_button(
                "Download transcript",
                data=json_block(st.session_state.transcript),
                file_name=Path(st.session_state.transcript_path).name,
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.caption("Transcript will be created on the first user turn.")

        render_rounds(st.session_state.turns)


if __name__ == "__main__":
    main()
