import json
import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="AI Promo Finder",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Streamlit Community Cloud stores credentials in st.secrets, not in .env.
# Copy only the required values into environment variables before importing
# promo_finder, because its provider configuration is read from os.environ.
try:
    for secret_name in (
        "GROQ_API_KEY",
        "AI_PROVIDER",
        "AI_MODEL",
        "AI_MAX_ESTIMATED_INPUT_TOKENS",
        "AI_MAX_OUTPUT_TOKENS",
    ):
        if secret_name in st.secrets:
            os.environ[secret_name] = str(st.secrets[secret_name])
except (FileNotFoundError, KeyError):
    # Local development can use a .env file instead.
    pass

from promo_finder import analyze_files  # noqa: E402

provider = os.getenv("AI_PROVIDER", "groq").strip().lower()
model = os.getenv("AI_MODEL", "openai/gpt-oss-120b").strip()
key_present = bool(os.getenv("GROQ_API_KEY")) if provider == "groq" else False

st.title("🎬 AI Promo Finder")
st.caption(
    "Find and rank distinct 20–60 second promotional moments from "
    "timestamped microdrama screenplays."
)

with st.sidebar:
    st.header("Analysis settings")
    st.write(f"**Provider:** {provider.title()}")
    st.write(f"**Model:** `{model}`")
    use_ai = st.toggle("Use AI creative evaluation", value=key_present)
    st.caption(
        "Timestamp parsing, candidate generation, duration checks, and "
        "overlap filtering run locally. Groq evaluates the shortlisted "
        "candidates in one request per episode."
    )
    if key_present:
        st.success("Groq API key detected.")
    else:
        st.warning("Groq API key is not configured. Add it in Streamlit Secrets.")
    st.caption("API credentials are never displayed in the app.")

st.subheader("Upload screenplays")
uploads = st.file_uploader(
    "Select one or more timestamped screenplay TXT files",
    type=["txt"],
    accept_multiple_files=True,
    help="You can upload the full episode pack together.",
)

if uploads:
    st.write(f"**{len(uploads)} screenplay file(s) selected**")
    if st.button("Analyze screenplays", type="primary", use_container_width=True):
        if use_ai and not key_present:
            st.error("AI evaluation is enabled, but GROQ_API_KEY is missing. Add it in Streamlit Secrets and reboot the app.")
        else:
            with st.spinner("Parsing screenplays and evaluating promotional candidates..."):
                results, errors, usage = analyze_files(uploads, use_ai=use_ai)
            st.session_state["promo_results"] = results
            st.session_state["promo_errors"] = errors
            st.session_state["promo_usage"] = usage

if "promo_results" in st.session_state:
    results = st.session_state.get("promo_results", [])
    errors = st.session_state.get("promo_errors", [])
    usage = st.session_state.get("promo_usage", [])

    st.divider()
    if errors:
        st.subheader("Processing notes")
        for error in errors:
            st.warning(error)

    if usage:
        st.subheader("AI token usage")
        usage_df = pd.DataFrame(usage)
        st.dataframe(usage_df, use_container_width=True, hide_index=True)
        st.caption(
            "Estimated input tokens are local preflight estimates. Actual token "
            "counts are shown only when returned by the provider."
        )

    if not results:
        st.error(
            "No promotional candidates were generated. Review the processing "
            "notes and check the Groq API quota or configuration."
        )
    else:
        st.subheader("Ranked promotional candidates")
        st.caption(
            "Scores are prioritization estimates, not predictions of social-media "
            "performance. Evidence is shown only when an exact screenplay excerpt is verified."
        )

        for rank, item in enumerate(results, start=1):
            with st.container(border=True):
                left, right = st.columns([5, 1])
                with left:
                    episode_label = str(item.get("episode", "Unknown episode"))
                    if not episode_label.lower().startswith("episode"):
                        episode_label = f"Episode {episode_label}"
                    st.markdown(f"### #{rank} — {episode_label}")
                    angle = item.get("story_angle", "unclassified")
                    st.write(f"**Story angle:** `{angle.replace('_', ' ').title()}`")
                    st.write(
                        f"**Time:** {item['start']} – {item['end']} · "
                        f"**Duration:** {item['duration_seconds']:.2f} sec"
                    )
                    st.write(f"**Hook:** {item.get('hook', 'Not evaluated')}")
                    st.write(f"**Cliffhanger:** {item.get('cliffhanger', 'Not evaluated')}")
                    st.write(f"**Why it works:** {item.get('reason', '')}")

                    overlaps = item.get("overlaps_with_selected_clips", [])
                    if overlaps:
                        most = max(
                            overlaps,
                            key=lambda x: x["overlap_percent_of_shorter_clip"],
                        )
                        st.caption(
                            f"Editorial overlap: {most['overlap_percent_of_shorter_clip']:.1f}% "
                            f"with {most['story_angle'].replace('_', ' ')}. Review both clips together."
                        )

                    evidence = item.get("evidence", [])
                    if evidence:
                        st.write("**Validated screenplay evidence:**")
                        for ev in evidence:
                            st.write(
                                f"- [{ev['type'].upper()} · {ev['time']}–{ev['end']}] "
                                f"{ev['quote']}"
                            )
                    else:
                        st.warning("No exact supporting excerpt passed evidence validation. Review manually.")

                    warnings = item.get("boundary_warnings", {})
                    if warnings.get("start_cuts_event") or warnings.get("end_cuts_event"):
                        st.warning(
                            "A clip boundary intersects a timed screenplay event "
                            "(including camera events). Check this cut before editing."
                        )
                    else:
                        st.caption("No timed event is cut at either boundary.")

                    if item.get("editorial_warning"):
                        st.warning(item["editorial_warning"])

                with right:
                    st.metric("Score", f"{item.get('score', 0)}/100")
                    st.caption(item.get("score_basis", "AI assessment"))

        st.download_button(
            "Download results as JSON",
            json.dumps(results, indent=2, ensure_ascii=False),
            file_name="promo_results.json",
            mime="application/json",
        )
        csv_rows = [
            {
                "rank": i,
                "episode": x.get("episode", ""),
                "story_angle": x.get("story_angle", ""),
                "start": x.get("start", ""),
                "end": x.get("end", ""),
                "duration_seconds": x.get("duration_seconds", 0),
                "score": x.get("score", 0),
                "hook": x.get("hook", ""),
                "cliffhanger": x.get("cliffhanger", ""),
                "reason": x.get("reason", ""),
                "evidence_validation": x.get("evidence_validation", ""),
            }
            for i, x in enumerate(results, start=1)
        ]
        st.download_button(
            "Download results as CSV",
            pd.DataFrame(csv_rows).to_csv(index=False),
            file_name="promo_results.csv",
            mime="text/csv",
        )
        if usage:
            st.download_button(
                "Download token usage as JSON",
                json.dumps(usage, indent=2),
                file_name="promo_token_usage.json",
                mime="application/json",
            )
