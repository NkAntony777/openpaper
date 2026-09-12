"""
Lightweight OpenDraft UI (Streamlit).
Provides model configuration, progress tracking, and output browsing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

from config import get_config


ENGINE_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ENGINE_ROOT / ".env.local"
RUNNER_PATH = ENGINE_ROOT / "ui" / "run_generate.py"


def read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_env_file(path: Path, updates: Dict[str, str]) -> None:
    existing = read_env_file(path)
    existing.update({k: v for k, v in updates.items() if v is not None})
    lines = [f"{k}={existing[k]}" for k in sorted(existing.keys())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch_models(base_url: str, api_key: str) -> List[str]:
    if not base_url:
        return []
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", []) if isinstance(payload, dict) else []
    models = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return models


def load_progress(progress_path: Path) -> Optional[Dict[str, Any]]:
    try:
        if progress_path.exists():
            return json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def tail_file(path: Path, max_lines: int = 400) -> str:
    try:
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


st.set_page_config(page_title="OpenDraft Studio", layout="wide")
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
    .block-container { padding-top: 1.5rem; }
    .stApp { background: radial-gradient(circle at top, #0f172a 0%, #0b1120 45%, #05070f 100%); color: #e2e8f0; }
    .panel { background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 14px; padding: 1.2rem; box-shadow: 0 12px 30px rgba(0,0,0,0.35); }
    .muted { color: #94a3b8; font-size: 0.9rem; }
    .section-title { font-weight: 600; font-size: 1.1rem; color: #e2e8f0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("OpenDraft Studio")
st.markdown("<div class='muted'>Configure models, run generations, and monitor progress in one place.</div>", unsafe_allow_html=True)

config = get_config()
env = read_env_file(ENV_PATH)

if "model_list" not in st.session_state:
    st.session_state["model_list"] = []
if "running" not in st.session_state:
    st.session_state["running"] = False
if "current_run" not in st.session_state:
    st.session_state["current_run"] = {}
if "last_output_dir" not in st.session_state:
    st.session_state["last_output_dir"] = None

with st.sidebar:
    st.markdown("<div class='section-title'>Model Configuration</div>", unsafe_allow_html=True)
    provider = st.selectbox(
        "Provider",
        options=["openai", "gemini", "claude"],
        index=0 if env.get("AI_PROVIDER", "openai") == "openai" else 1,
    )
    base_url = st.text_input(
        "Base URL",
        value=env.get("OPENAI_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")),
    )
    api_key = st.text_input(
        "API Key",
        value=env.get("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        type="password",
    )

    model_name = st.text_input(
        "Model",
        value=env.get("OPENAI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-5.4")),
    )

    if st.button("Fetch Models"):
        try:
            st.session_state["model_list"] = fetch_models(base_url, api_key)
            st.success(f"Loaded {len(st.session_state['model_list'])} models.")
        except Exception as e:
            st.error(f"Fetch failed: {e}")

    if st.session_state["model_list"]:
        model_name = st.selectbox(
            "Pick Model",
            options=st.session_state["model_list"],
            index=st.session_state["model_list"].index(model_name) if model_name in st.session_state["model_list"] else 0,
        )

    if st.button("Save Configuration"):
        write_env_file(ENV_PATH, {
            "AI_PROVIDER": provider,
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": api_key,
            "OPENAI_MODEL": model_name,
            "ALLOW_CUSTOM_OPENAI_MODEL": "true",
        })
        st.success("Saved to .env.local")

    if st.button("Check Availability"):
        try:
            models = fetch_models(base_url, api_key)
            if model_name in models:
                st.success("Model is available.")
            else:
                st.error("Model not found in provider list.")
        except Exception as e:
            st.error(f"Check failed: {e}")


left, right = st.columns([1.2, 1])

with left:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>New Generation</div>", unsafe_allow_html=True)

    with st.form("generation_form"):
        topic = st.text_area("Research Topic", height=90)
        blurb = st.text_area("Research Focus (optional)", height=70)

        academic_level = st.selectbox(
            "Academic Level",
            options=["research_paper", "bachelor", "master", "phd"],
            index=2,
        )

        output_type = st.selectbox(
            "Output Type",
            options=["full", "expose"],
            index=0,
        )

        citation_style = st.selectbox(
            "Citation Style",
            options=["apa", "ieee", "chicago", "mla"],
            index=0,
        )

        language = st.selectbox(
            "Language",
            options=["en", "de", "es", "fr", "zh", "ja", "ko", "other"],
            index=0,
        )
        custom_language = ""
        if language == "other":
            custom_language = st.text_input("Custom Language Code", value="en")

        validate = st.checkbox("Enable strict validation", value=False)

        output_dir_input = st.text_input(
            "Output Directory",
            value=str(config.paths.output_dir / "generated_draft"),
        )

        resume_from = st.text_input("Resume from checkpoint.json (optional)", value="")

        with st.expander("Cover Page Details"):
            author_name = st.text_input("Author")
            institution = st.text_input("Institution")
            department = st.text_input("Department")
            faculty = st.text_input("Faculty")
            advisor = st.text_input("Advisor")
            second_examiner = st.text_input("Second Examiner")
            location = st.text_input("Location")
            student_id = st.text_input("Student ID")

        submitted = st.form_submit_button("Start Generation")

        if submitted:
            if not topic.strip():
                st.error("Please enter a research topic.")
            else:
                run_id = time.strftime("%Y%m%d_%H%M%S")
                output_dir = Path(output_dir_input).expanduser().resolve()
                output_dir.mkdir(parents=True, exist_ok=True)
                params = {
                    "run_id": run_id,
                    "topic": topic.strip(),
                    "blurb": blurb.strip(),
                    "academic_level": academic_level,
                    "output_type": output_type,
                    "citation_style": citation_style,
                    "language": custom_language.strip() if language == "other" else language,
                    "validate": validate,
                    "output_dir": str(output_dir),
                    "resume_from": resume_from.strip(),
                    "author_name": author_name.strip(),
                    "institution": institution.strip(),
                    "department": department.strip(),
                    "faculty": faculty.strip(),
                    "advisor": advisor.strip(),
                    "second_examiner": second_examiner.strip(),
                    "location": location.strip(),
                    "student_id": student_id.strip(),
                }
                params_path = output_dir / "ui_params.json"
                params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")

                env_vars = os.environ.copy()
                env_vars["OPENDRAFT_LOCAL_PROGRESS"] = "1"
                env_vars["OPENDRAFT_LOCAL_PROGRESS_ID"] = run_id

                log_path = output_dir / "ui_run.log"
                log_file = open(log_path, "a", encoding="utf-8")

                process = subprocess.Popen(
                    [sys.executable, str(RUNNER_PATH), "--params", str(params_path)],
                    cwd=str(ENGINE_ROOT),
                    env=env_vars,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                log_file.close()
                st.session_state["running"] = True
                st.session_state["current_run"] = {
                    "run_id": run_id,
                    "output_dir": str(output_dir),
                    "pid": process.pid,
                    "log_path": str(log_path),
                }
                st.session_state["last_output_dir"] = str(output_dir)
                st.success(f"Started generation (PID {process.pid}).")

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>Progress</div>", unsafe_allow_html=True)
    output_root = config.paths.output_dir
    output_dirs = []
    if output_root.exists():
        output_dirs = sorted([p for p in output_root.iterdir() if p.is_dir()])

    selected_dir = None
    if st.session_state["last_output_dir"]:
        selected_dir = Path(st.session_state["last_output_dir"])
    elif output_dirs:
        selected_dir = output_dirs[-1]

    if selected_dir:
        progress_file = selected_dir / "progress.json"
        progress = load_progress(progress_file) or {}
        st.metric("Phase", progress.get("phase") or "unknown")
        st.metric("Progress", f"{progress.get('progress_percent', 0)}%")
        st.json(progress.get("progress_details", {}))
    else:
        st.info("No runs yet.")

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>Live Logs</div>", unsafe_allow_html=True)
    auto_refresh = st.checkbox("Auto refresh (2s)", value=False)

    log_path = None
    if st.session_state.get("current_run", {}).get("log_path"):
        log_path = Path(st.session_state["current_run"]["log_path"])
    elif selected_dir:
        log_path = selected_dir / "ui_run.log"

    if log_path and log_path.exists():
        log_text = tail_file(log_path, max_lines=500)
        st.code(log_text or "(log file empty)", language="text")
    else:
        st.info("No log file yet.")

    if auto_refresh:
        time.sleep(2)
        st.experimental_rerun()

    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<div class='panel'>", unsafe_allow_html=True)
st.markdown("<div class='section-title'>Outputs</div>", unsafe_allow_html=True)
output_root = config.paths.output_dir
if output_root.exists():
    output_dirs = sorted([p for p in output_root.iterdir() if p.is_dir()])
    if output_dirs:
        selected_dir = st.selectbox("Browse Folder", options=output_dirs, format_func=lambda p: p.name, key="browse_dir")
        all_files = sorted([p for p in selected_dir.rglob("*") if p.is_file()])
        if all_files:
            selected_file = st.selectbox("File", options=all_files, format_func=lambda p: p.relative_to(selected_dir))
            if selected_file.suffix.lower() in {".md", ".txt", ".json"}:
                st.code(selected_file.read_text(encoding="utf-8"), language="markdown")
            else:
                st.write(f"File: {selected_file.name} ({selected_file.stat().st_size} bytes)")
                st.download_button("Download", data=selected_file.read_bytes(), file_name=selected_file.name)
        else:
            st.info("No files found in this output folder.")
    else:
        st.info("No output folders found yet.")
else:
    st.info(f"Output directory not found: {output_root}")

st.markdown("</div>", unsafe_allow_html=True)
