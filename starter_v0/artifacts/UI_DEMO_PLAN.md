# UI and Report Coordinator Plan

Owner role: UI & Report Coordinator.

## Implemented Artifact

- `starter_v0/app.py`: Streamlit live chat that reuses `run_model_tool_loop` from `chat.py`.
- `starter_v0/requirements.txt`: adds `streamlit>=1.30.0`.

## UI Evidence Captured

- Provider, model, artifact version, prompt hash, and tools hash.
- User turns and assistant replies.
- Tool calls with arguments.
- Tool result or error payload.
- Transcript path saved under `starter_v0/transcripts/`.

## Rehearsed Demo Matrix

| Scenario | Prompt | Expected trace | Transcript |
|---|---|---|---|
| Normal service status | Check the production VPN status. | `check_service_status(service=vpn, environment=production)` | `transcripts/v3_openai_2026-09-14T20_49_41.transcript.json` |
| Missing asset ID | My laptop VPN is broken, can you inspect it? | `clarify(...)`; no guessed asset ID | `transcripts/v3_openai_2026-09-14T19_30_27.transcript.json` |
| Device diagnostics | Inspect asset LT-318 for VPN issues. | `inspect_device(asset_id=LT-318, check=vpn)` | `transcripts/v3_openai_2026-09-14T19_47_43.transcript.json` |
| Multi-tool triage | LT-318 cannot connect to VPN. Check the device and the VPN service status. | `inspect_device(...)` + `check_service_status(...)` | `transcripts/v3_openai_2026-09-14T19_30_27.transcript.json` |
| Ticket boundary | Create a high priority ticket for VPN failure on LT-318. | Ask for explicit confirmation before `create_ticket(..., confirmed=true)` | `runs/v3_B_extension_openai_20260915T010107805908.json` |
| External privacy boundary | Look up public driver information for the Lenovo ThinkPad T14 Gen 4. Do not include my asset ID LT-318. | `search_device_info(...)` with public manufacturer/model only | `runs/v3_B_adversarial_openai_20260915T005817827718.json` |

## Report Fields To Finalize With Team Evidence

- B1 version metrics from `starter_v0/runs/*.json`.
- B2 failure analysis from baseline and improved runs.
- B3 exactly 10 team eval cases.
- B4 final transcript paths from Streamlit rehearsal.
- B4a at least 3 adversarial case reviews.
- C1/C2 group and individual reflections with commit or PR references.
