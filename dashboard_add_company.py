"""
dashboard_add_company.py -- "Add a company" panel for the Streamlit dashboard.

Playwright can't run inside Streamlit (sync API refuses to start off the main
thread), so this shells out to add_company.py in its own process and streams
the output back. Import render_add_company_panel() into dashboard.py and call
it wherever you want the panel (e.g. in the sidebar or a tab).
"""
import os
import sys
import json
import subprocess
import streamlit as st

BASE = os.path.dirname(os.path.abspath(__file__))
ADD_SCRIPT = os.path.join(BASE, "add_company.py")


def _run(company, mode_args):
    """Run add_company.py as a subprocess, streaming stdout into the UI."""
    cmd = [sys.executable, ADD_SCRIPT, company] + mode_args + ["--json"]
    log_area = st.empty()
    lines = []
    result = None

    try:
        proc = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        st.error(f"Could not launch add_company.py: {e}")
        return None

    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.startswith("RESULT_JSON:"):
            try:
                result = json.loads(line[len("RESULT_JSON:"):])
            except Exception:
                result = None
            continue
        lines.append(line)
        # live-updating log box (last 20 lines so it doesn't grow unbounded)
        log_area.code("\n".join(lines[-20:]) or "starting...", language="text")

    proc.wait()
    return result


def _show_result(result):
    if not result:
        st.warning("The process finished but returned no structured result. "
                   "Check the log above.")
        return

    verdict = result.get("verdict")
    company = result.get("company", "the company")
    platform = result.get("platform")
    reason = result.get("reason", "")

    if verdict == "verified":
        st.success(f"✅ **{company}** added and verified via {platform}. "
                   f"Live on the next scheduled run.")
        st.caption(reason)
        if result.get("sample"):
            st.write("Sample of what it found:")
            for j in result["sample"]:
                st.write(f"• {j['title']} — *{j['location']}*")

    elif verdict == "empty":
        st.warning(f"⚠️ **{company}** ({platform}) ran cleanly but found "
                   f"**0 US internships**.")
        st.caption(reason)
        st.info(f"Saved as `{result.get('file')}` but **not** added to the "
                f"nightly run. This is often genuine (no openings right now), "
                f"but review it before trusting.")

    elif verdict == "error":
        st.error(f"❌ **{company}** ({platform}) generated a scraper, but the "
                 f"live check failed.")
        st.caption(reason)
        st.info(f"Quarantined as `{result.get('file')}`. Needs a look before use.")

    elif verdict == "no_template":
        st.warning(f"🔧 **{company}** uses **{platform}**, which doesn't have an "
                   f"auto-template yet.")
        st.caption(reason)
        st.info("Detected but not generated — this one needs a manual scraper "
                "(the Ford / Lockheed class of site).")

    elif verdict == "detected":
        st.info(f"🔍 Detected **{platform}** for **{company}** (dry run — "
                f"nothing written).")
        
    elif verdict == "exists":
        st.info(f"ℹ️ **{company}** already has a scraper — `{result.get('file')}`. "
                f"Nothing was written.")
        st.caption(reason)

    else:  # undetected
        st.error(f"❔ Couldn't detect a known job board for **{company}**.")
        st.caption(reason or "")
        st.info("Try providing the careers-page URL, or add it by hand.")


def render_add_company_panel():
    st.subheader("➕ Add a company")
    st.caption("Detects the job board, generates a scraper, and runs a live "
               "check. Only companies that pass the live check are added to the "
               "nightly run automatically.")

    company = st.text_input("Company name", placeholder="e.g. Roush")

    with st.expander("Options (optional)"):
        url = st.text_input("Careers page URL",
                            placeholder="https://…/careers — helps if auto-detect fails")
        col1, col2, col3 = st.columns(3)
        gh = col1.text_input("Greenhouse token", placeholder="if known")
        lv = col2.text_input("Lever token", placeholder="if known")
        ash = col3.text_input("Ashby token", placeholder="if known")
        dry = st.checkbox("Dry run (detect only, write nothing)")
        force = st.checkbox("Force (replace an existing scraper)")

    disabled = not company.strip()
    if st.button("Detect & add", type="primary", disabled=disabled):
        mode = []
        if gh.strip():   mode += ["--greenhouse", gh.strip()]
        elif lv.strip(): mode += ["--lever", lv.strip()]
        elif ash.strip(): mode += ["--ashby", ash.strip()]
        if url.strip():  mode += ["--url", url.strip()]
        if dry:          mode += ["--dry-run"]
        if force:        mode += ["--force"]

        with st.spinner(f"Working on {company}… (this can take 30–60s while it "
                        f"checks the live site)"):
            result = _run(company.strip(), mode)
        _show_result(result)

    # surface any quarantined files awaiting review
    scr = os.path.join(BASE, "scrapers")
    if os.path.isdir(scr):
        pending = [f for f in os.listdir(scr) if f.endswith(".unverified")]
        if pending:
            st.divider()
            st.caption("**Awaiting review** (generated but not verified):")
            for f in sorted(pending):
                st.write(f"• `{f}`")