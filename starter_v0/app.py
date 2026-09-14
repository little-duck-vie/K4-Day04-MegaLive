from __future__ import annotations

import json
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

DEMO_SCENARIOS = {
    "Service status": "Check the production VPN status.",
    "Missing asset ID": "My laptop VPN is broken, can you inspect it?",
    "Device diagnostics": "Inspect asset LT-318 for VPN issues.",
    "Multi-tool triage": "LT-318 cannot connect to VPN. Check the device and the VPN service status.",
    "Ticket boundary": "Create a high priority ticket for VPN failure on LT-318.",
    "External privacy boundary": (
        "Look up public driver information for the Lenovo ThinkPad T14 Gen 4. "
        "Do not include my asset ID LT-318."
    ),
}


def json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


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

    st.title("IT Helpdesk Agent")
    st.caption("Live chat demo with tool calls, arguments, results, and transcript evidence.")

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
        st.subheader("Demo Prompts")
        for label, prompt in DEMO_SCENARIOS.items():
            if st.button(label, use_container_width=True):
                st.session_state.pending_prompt = prompt

    model_value = model.strip() or None
    try:
        artifact_version = build_artifact_version(version, system_prompt_path, tools_path)
        st.info(f"Artifact version: `{artifact_version.artifact_version}`")
    except FileNotFoundError as exc:
        st.error(f"Artifact file not found: {exc}")
        return

    left, right = st.columns([0.58, 0.42], gap="large")

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
        if st.session_state.transcript_path:
            st.caption("Transcript")
            st.code(str(st.session_state.transcript_path), language="text")
        else:
            st.caption("Transcript will be created on the first user turn.")

        render_rounds(st.session_state.turns)


if __name__ == "__main__":
    main()
