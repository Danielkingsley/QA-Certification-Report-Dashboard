import json
import os
import smtplib
import streamlit as st
from jira import JIRA
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def _secret(key: str) -> str:
    """Read from st.secrets first, fall back to env var."""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

st.set_page_config(page_title="QA Certification Report", page_icon="📋", layout="wide")

# ── Password gate ─────────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"):
        return
    _, col, _ = st.columns([1, 1, 1])
    with col:
        st.markdown("### 🔒 QA Certification Report")
        st.caption("Enter the password to access the dashboard.")
        pwd = st.text_input("Password", type="password", placeholder="Password",
                            label_visibility="collapsed", key="_pwd", max_chars=15)
        if st.button("Login", type="primary", use_container_width=True):
            if pwd == st.secrets.get("APP_PASSWORD", ""):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()

check_password()

JIRA_BASE_URL    = _secret("JIRA_BASE_URL")
JIRA_USERNAME    = _secret("JIRA_USERNAME")
JIRA_API_TOKEN   = _secret("JIRA_API_TOKEN")
SMTP_SERVER   = "smtp.office365.com"
SMTP_PORT     = 587
SENDER_EMAIL  = _secret("JIRA_USERNAME")
SENDER_PASSWORD = _secret("SENDER_PASSWORD")

CONFIG_FILE  = os.path.join(os.path.expanduser("~"), ".qa_report_config.json")
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))

def load_config():
    return {"presets_dir": SCRIPT_DIR}

def save_config(cfg):
    pass  # no-op on cloud; path setting is local-only

_cfg = load_config()
PRESETS_FILE = os.path.join(_cfg["presets_dir"], "presets.json")

EXAMPLE_FILTER_JSON = [
    {
        "label": "Prod Testing",
        "jql": "fixVersion = 9.3 and labels in (PROD_Testing)",
        "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee",
        "section_title": "Tickets marked for Prod_Testing:"
    },
    {
        "label": "No QA",
        "jql": "fixVersion = 9.3 and labels in (no_qa)",
        "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee",
        "section_title": "Below tickets are marked as NO_QA:"
    }
]

EXAMPLE_PRESET_JSON = [
    {
        "name": "My Report",
        "fix_version": "9.3",
        "greeting": "Hi All,",
        "intro": "The QA team has completed testing for the <b>{month}</b> release (<b>{version}</b>).",
        "footer": "Best regards,<br>QA Team",
        "filters": [
            {
                "label": "Prod Testing",
                "jql": "fixVersion = {version} and labels in (PROD_Testing)",
                "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee",
                "section_title": "Tickets marked for Prod_Testing:"
            },
            {
                "label": "No QA",
                "jql": "fixVersion = {version} and labels in (no_qa)",
                "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee",
                "section_title": "Below tickets are marked as NO_QA:"
            }
        ]
    }
]

# ── Preset helpers ────────────────────────────────────────────────────────────
def get_presets_file():
    return PRESETS_FILE

def load_presets():
    # Runtime: use session state cache so edits survive reruns
    if "_presets_cache" in st.session_state:
        return st.session_state["_presets_cache"]
    # First load: read from repo file
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE, "r") as f:
            data = json.load(f)
    else:
        data = []
    st.session_state["_presets_cache"] = data
    return data

def save_presets(presets):
    # Always update session state cache
    st.session_state["_presets_cache"] = presets
    # Also write to disk if writable (local dev)
    try:
        with open(PRESETS_FILE, "w") as f:
            json.dump(presets, f, indent=2)
    except OSError:
        pass  # read-only on Streamlit Cloud — session state is the store

def export_presets_json():
    return json.dumps(load_presets(), indent=2)

def apply_version(text, version):
    return text.replace("{version}", version)

def resolve_preset(preset, version):
    """Return a copy of preset with {version} substituted in all JQLs."""
    resolved = json.loads(json.dumps(preset))  # deep copy
    for flt in resolved["filters"]:
        flt["jql"] = apply_version(flt["jql"], version)
    resolved["intro"] = apply_version(resolved.get("intro", ""), version).replace("{month}", datetime.now().strftime("%B %Y"))
    return resolved

def clear_filter_widget_keys():
    """Remove filter widget keys so Streamlit re-renders with new values."""
    for key in list(st.session_state.keys()):
        if any(key.startswith(p) for p in ("label_", "jql_", "stitle_", "headers_")):
            del st.session_state[key]

def make_backup():
    return json.dumps({
        "filters":     st.session_state.filters,
        "greeting":    st.session_state.greeting,
        "intro":       st.session_state.intro,
        "footer":      st.session_state.footer,
        "email_style": st.session_state.email_style,
        "exported_at": datetime.now().isoformat(),
    }, indent=2)

# ── Session state ─────────────────────────────────────────────────────────────
defaults = {
    "filters": [
        {"label": "Prod Testing",  "jql": "fixVersion = 9.3 and labels in (PROD_Testing)",
         "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee", "section_title": "Tickets marked for Prod_Testing:"},
        {"label": "No QA",         "jql": "fixVersion = 9.3 and labels in (no_qa)",
         "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee", "section_title": "Below tickets are marked as NO_QA:"},
        {"label": "All QA Issues", "jql": "fixVersion = 9.3 and labels not in (PROD_Testing, no_qa)",
         "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee", "section_title": "Below tickets are all QA issues:"},
    ],
    "html_preview": "",
    "email_content": {},
    "email_style": "Styled",
    "greeting": "Hi All,",
    "intro": f"The QA team has completed regression testing and certifies the <b>{datetime.now().strftime('%B %Y')}</b> release in the S1 environment.",
    "footer": "Best regards,<br>QA Team",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Jira / data helpers ───────────────────────────────────────────────────────
@st.cache_resource
def get_jira():
    return JIRA(server=JIRA_BASE_URL, basic_auth=(JIRA_USERNAME, JIRA_API_TOKEN))

def fetch_issues(jql):
    try:
        return get_jira().enhanced_search_issues(jql, maxResults=False)
    except Exception as e:
        st.error(f"Jira error: {e}")
        return []

COL_WIDTHS = {"s.no": "4%", "jira id": "8%", "summary": "42%", "issue type": "10%",
              "status": "10%", "assignee": "14%", "priority": "7%", "project": "7%", "labels": "12%"}

def build_rows(issues, headers):
    if not issues:
        return ""
    cols = [h.strip() for h in headers.split(",")]
    by_assignee = {}
    for issue in issues:
        name = issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned"
        by_assignee.setdefault(name, []).append(issue)
    by_assignee = dict(sorted(by_assignee.items(), key=lambda x: (x[0] == "Unassigned", x[0])))
    col_map = {
        "s.no":       lambda i, iss: str(i),
        "jira id":    lambda i, iss: '<a href="{}/browse/{}">{}</a>'.format(JIRA_BASE_URL, iss.key, iss.key),
        "summary":    lambda i, iss: iss.fields.summary,
        "issue type": lambda i, iss: iss.fields.issuetype.name,
        "status":     lambda i, iss: iss.fields.status.name,
        "assignee":   lambda i, iss: iss.fields.assignee.displayName if iss.fields.assignee else "Unassigned",
        "priority":   lambda i, iss: getattr(iss.fields.priority, "name", ""),
        "project":    lambda i, iss: iss.fields.project.key,
        "labels":     lambda i, iss: ", ".join(iss.fields.labels or []),
    }
    header_row = "".join("<th>{}</th>".format(c) for c in cols)
    rows = "<tr>{}</tr>".format(header_row)
    idx = 1
    for assignee_issues in by_assignee.values():
        for issue in assignee_issues:
            cells = "".join(
                "<td>{}</td>".format(col_map.get(c.lower(), lambda i, iss: "")(idx, issue))
                for c in cols
            )
            rows += "<tr>{}</tr>".format(cells)
            idx += 1
    return rows

def make_table(rows, headers):
    if not rows:
        return "<p><i>No issues found.</i></p>"
    cols = [h.strip() for h in headers.split(",")]
    colgroup = "".join('<col style="width:{};">'.format(COL_WIDTHS.get(c.lower(), "auto")) for c in cols)
    return "<table><colgroup>{}</colgroup>{}</table>".format(colgroup, rows)

def build_html_styled(greeting, intro, filters_data, footer, fix_version, current_month):
    sections = ""
    for fd in filters_data:
        if fd.get("issues") is not None:
            rows  = build_rows(fd["issues"], fd["headers"])
            table = make_table(rows, fd["headers"])
            sections += "<p><b>{}</b></p>{}".format(fd["section_title"], table)
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body{{font-family:'Segoe UI',Arial,sans-serif;font-size:13px;color:#222;background:#f4f6fb;margin:0;padding:0;}}
  .ew{{max-width:1600px;margin:30px auto;background:#fff;border-radius:10px;box-shadow:0 2px 16px rgba(0,0,0,.10);overflow:hidden;}}
  .eh{{background:linear-gradient(90deg,#1a3a6b 0%,#4472C4 100%);padding:28px 36px 18px;}}
  .eh h2{{color:#fff;margin:0;font-size:20px;letter-spacing:.5px;}}
  .eh p{{color:#c9d8f5;margin:4px 0 0;font-size:12px;}}
  .eb{{padding:28px 36px;}}
  .eb p{{margin:0 0 12px;line-height:1.6;}}
  table{{border-collapse:collapse;width:100%;margin-bottom:22px;font-size:12px;}}
  th{{background:#4472C4;color:#fff;padding:9px 10px;text-align:left;font-weight:600;}}
  td{{border:1px solid #dde3f0;padding:7px 10px;vertical-align:top;}}
  tr:nth-child(even) td{{background:#f0f4fb;}}
  tr:hover td{{background:#e3ecfa;}}
  a{{color:#1a3a6b;text-decoration:none;font-weight:600;}}
  a:hover{{text-decoration:underline;}}
  .ef{{background:#f0f4fb;border-top:1px solid #dde3f0;padding:14px 36px;font-size:11px;color:#888;}}
</style></head><body>
<div class="ew">
  <div class="eh">
    <h2>📋 QA Certification Report</h2>
    <p>Release: <b>{fix_version}</b> &nbsp;|&nbsp; {current_month}</p>
  </div>
  <div class="eb">
    <p>{greeting}</p>
    <p>{intro}</p>
    {sections}
    <p>{footer}</p>
  </div>
  <div class="ef">This is an automated report generated by the QA Certification Dashboard.</div>
</div></body></html>""".format(
        fix_version=fix_version, current_month=current_month,
        greeting=greeting, intro=intro, sections=sections, footer=footer)

def build_html_plain(greeting, intro, filters_data, footer, fix_version, current_month):
    sections = ""
    for fd in filters_data:
        if fd.get("issues") is not None:
            rows  = build_rows(fd["issues"], fd["headers"])
            table = make_table(rows, fd["headers"])
            sections += "<p><b>{}</b></p>{}".format(fd["section_title"], table)
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body{{font-family:Arial,sans-serif;font-size:13px;color:#000;background:#fff;margin:20px;padding:0;}}
  table{{border-collapse:collapse;width:100%;margin-bottom:18px;font-size:12px;}}
  th{{background-color: #4472C4; color: white; border:1px solid #000;padding:6px 8px;text-align:center;font-weight:bold;}}
  td{{border:1px solid #000;padding:6px 8px;vertical-align:top;}}
  a{{color:#00f;}}
  p{{margin:0 0 10px;}}
</style></head><body>
<p>{greeting}</p>
<p>{intro}</p>
{sections}
<p>{footer}</p>
</body></html>""".format(
        greeting=greeting, intro=intro, sections=sections, footer=footer)

def build_html(greeting, intro, filters_data, footer, fix_version, current_month, styled=True):
    if styled:
        return build_html_styled(greeting, intro, filters_data, footer, fix_version, current_month)
    return build_html_plain(greeting, intro, filters_data, footer, fix_version, current_month)

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ Configuration")

    # ── Storage path ─────────────────────────────────────────────────────────
    with st.expander("📁 Presets Storage"):
        st.info("Presets are stored in session memory on Streamlit Cloud. Use **Export Presets** to save them locally and **Import** to restore after a restart.")
        col_exp, col_imp = st.columns(2)
        with col_exp:
            st.download_button(
                "⬇ Export Presets",
                data=export_presets_json(),
                file_name="presets.json",
                mime="application/json",
                use_container_width=True,
                help="Download your saved presets to keep them permanently"
            )
        with col_imp:
            up_presets = st.file_uploader("Import presets.json", type="json", key="preset_uploader",
                                          label_visibility="collapsed")
            if up_presets:
                try:
                    imported = json.load(up_presets)
                    if isinstance(imported, list):
                        save_presets(imported)
                        st.success("Presets imported: {}".format(len(imported)))
                        st.rerun()
                    else:
                        st.error("Must be a list of preset objects.")
                except Exception as e:
                    st.error(str(e))
    st.divider()

    # ── Email output style toggle ─────────────────────────────────────────────
    st.session_state.email_style = st.radio(
        "Email Output Style", ["Plain", "Styled"], horizontal=True,
        index=0 if st.session_state.email_style == "Plain" else 1,
        help="Plain: simple text and basic bordered table | Styled: gradient header + colored table"
    )
    st.divider()

    # ── Presets / Bookmarks ───────────────────────────────────────────────────
    st.subheader("🔖 Report Presets")
    presets = load_presets()
    preset_names = [p["name"] for p in presets]

    selected_preset = st.selectbox("Load Preset", ["— select —"] + preset_names)
    fix_version_override = st.text_input("Fix Version (overrides preset)", value="9.3",
                                         help="Only the fix version changes between releases")

    col_load, col_del = st.columns(2)
    with col_load:
        if st.button("▶ Load", use_container_width=True, disabled=selected_preset == "— select —"):
            preset = next(p for p in presets if p["name"] == selected_preset)
            resolved = resolve_preset(preset, fix_version_override)
            st.session_state.filters  = resolved["filters"]
            st.session_state.greeting = preset.get("greeting", defaults["greeting"])
            st.session_state.intro    = resolved.get("intro", defaults["intro"])
            st.session_state.footer   = preset.get("footer", defaults["footer"])
            clear_filter_widget_keys()  # force widget re-render with new values
            st.success(f"Loaded: {selected_preset}")
            st.rerun()
    with col_del:
        if st.button("🗑 Delete", use_container_width=True, disabled=selected_preset == "— select —"):
            presets = [p for p in presets if p["name"] != selected_preset]
            save_presets(presets)
            st.success(f"Deleted: {selected_preset}")
            st.rerun()

    # ── Save current as preset ────────────────────────────────────────────────
    with st.expander("💾 Save Current as Preset"):
        new_preset_name = st.text_input("Preset Name", placeholder="e.g. Mobile, Regression")
        if st.button("Save Preset", use_container_width=True):
            if new_preset_name.strip():
                # Store JQLs with {version} placeholder substituted back
                template_filters = []
                for flt in st.session_state.filters:
                    tf = flt.copy()
                    tf["jql"] = tf["jql"].replace(fix_version_override, "{version}")
                    template_filters.append(tf)
                new_preset = {
                    "name": new_preset_name.strip(),
                    "fix_version": fix_version_override,
                    "greeting": st.session_state.greeting,
                    "intro": st.session_state.intro.replace(fix_version_override, "{version}"),
                    "footer": st.session_state.footer,
                    "filters": template_filters,
                }
                presets = [p for p in load_presets() if p["name"] != new_preset_name.strip()]
                presets.append(new_preset)
                save_presets(presets)
                st.success(f"Saved preset: {new_preset_name.strip()}")
                st.rerun()
            else:
                st.warning("Enter a preset name.")

    st.divider()

    # ── Release info ──────────────────────────────────────────────────────────
    st.subheader("📅 Release Info")
    fix_version   = st.text_input("Fix Version", value=fix_version_override)
    current_month = st.text_input("Release Month", value=datetime.now().strftime("%B %Y"))

    st.divider()

    # ── Email settings ────────────────────────────────────────────────────────
    st.subheader("📧 Email Settings")
    to_emails = st.text_area("To (comma-separated)", value="")
    smtp_user = SENDER_EMAIL
    smtp_pass = SENDER_PASSWORD
    subject   = st.text_input("Subject", value="QA Certification Report: {}".format(fix_version))

    st.divider()

    # ── Upload / Backup ───────────────────────────────────────────────────────
    st.subheader("📂 Import / Export")

    upload_tab, backup_tab = st.tabs(["⬆ Import", "⬇ Export"])

    with upload_tab:
        st.caption("Upload a filter JSON or a full backup JSON.")
        uploaded = st.file_uploader("Choose file", type="json", key="uploader")
        if uploaded:
            try:
                loaded = json.load(uploaded)
                # Full backup format
                if isinstance(loaded, dict) and "filters" in loaded:
                    st.session_state.filters     = loaded["filters"]
                    st.session_state.greeting    = loaded.get("greeting",    defaults["greeting"])
                    st.session_state.intro       = loaded.get("intro",       defaults["intro"])
                    st.session_state.footer      = loaded.get("footer",      defaults["footer"])
                    st.session_state.email_style = loaded.get("email_style", defaults["email_style"])
                    clear_filter_widget_keys()
                    st.success("Full backup restored.")
                    st.rerun()
                # Filter-only list format
                elif isinstance(loaded, list) and len(loaded) <= 5:
                    st.session_state.filters = loaded
                    clear_filter_widget_keys()
                    st.success(f"Loaded {len(loaded)} filters.")
                    st.rerun()
                else:
                    st.error("Must be a backup object or a list of up to 5 filters.")
            except Exception as e:
                st.error(f"Invalid JSON: {e}")

    with backup_tab:
        st.caption("Download all current filters and settings.")
        st.download_button(
            "⬇ Download Backup",
            data=make_backup(),
            file_name="qa_report_backup_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S")),
            mime="application/json",
            use_container_width=True,
        )

    st.divider()
    st.subheader("📄 Example Files")
    st.download_button(
        "Example filter JSON",
        data=json.dumps(EXAMPLE_FILTER_JSON, indent=2),
        file_name="example_filter.json",
        mime="application/json",
        use_container_width=True,
    )
    st.download_button(
        "Example preset JSON",
        data=json.dumps(EXAMPLE_PRESET_JSON, indent=2),
        file_name="example_preset.json",
        mime="application/json",
        use_container_width=True,
    )

    st.divider()
    if st.button("🔄 Reset Filters to Default"):
        st.session_state.filters = [f.copy() for f in defaults["filters"]]
        clear_filter_widget_keys()
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
st.title("📋 QA Certification Report Dashboard")
st.caption("Load a preset, adjust the fix version, preview and send.")

# ── Editable email content ────────────────────────────────────────────────────
with st.expander("✏️ Edit Email Content", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.greeting = st.text_input("Greeting", value=st.session_state.greeting)
        st.session_state.intro    = st.text_area("Introduction", value=st.session_state.intro, height=90)
    with c2:
        st.session_state.footer = st.text_area("Footer / Sign-off", value=st.session_state.footer, height=90)

greeting = st.session_state.greeting
intro    = st.session_state.intro
footer   = st.session_state.footer

# ── Filter management ─────────────────────────────────────────────────────────
st.subheader("🔍 Filters")
col_add, _ = st.columns([1, 5])
with col_add:
    if st.button("➕ Add Filter", disabled=len(st.session_state.filters) >= 5):
        st.session_state.filters.append({
            "label": "New Filter", "jql": "",
            "headers": "S.No,Jira ID,Summary,Issue Type,Status,Assignee",
            "section_title": "Issues:"
        })
        st.rerun()

filters_to_delete = []
num_filters = len(st.session_state.filters)

cols = st.columns(num_filters)
for i, f in enumerate(st.session_state.filters):
    with cols[i]:
        with st.container(border=True):
            st.text_input("Label",         value=f["label"],         key="label_{}".format(i))
            st.text_area("JQL",            value=f["jql"],           key="jql_{}".format(i), height=90)
            st.text_input("Section Title", value=f["section_title"], key="stitle_{}".format(i))
            st.text_input("Headers",       value=f["headers"],       key="headers_{}".format(i),
                          help="S.No,Jira ID,Summary,Issue Type,Status,Assignee,Priority,Project,Labels")
            if st.button("🗑️ Remove", key="del_{}".format(i), disabled=num_filters <= 1):
                filters_to_delete.append(i)

if filters_to_delete:
    st.session_state.filters = [f for i, f in enumerate(st.session_state.filters) if i not in filters_to_delete]
    st.rerun()

# Sync widget values back to session state
for i, f in enumerate(st.session_state.filters):
    f["label"]         = st.session_state.get("label_{}".format(i),   f["label"])
    f["jql"]           = st.session_state.get("jql_{}".format(i),     f["jql"])
    f["section_title"] = st.session_state.get("stitle_{}".format(i),  f["section_title"])
    f["headers"]       = st.session_state.get("headers_{}".format(i), f["headers"])

# ── Generate ──────────────────────────────────────────────────────────────────
st.divider()
if st.button("🚀 Generate Report", type="primary", use_container_width=True):
    filters_data = []
    progress = st.progress(0, text="Fetching Jira issues...")
    total = len(st.session_state.filters)
    for idx, f in enumerate(st.session_state.filters):
        progress.progress(idx / total, text="Fetching: {}...".format(f["label"]))
        issues = fetch_issues(f["jql"]) if f["jql"].strip() else []
        filters_data.append({**f, "issues": issues})
        progress.progress((idx + 1) / total, text="Done: {} ({} issues)".format(f["label"], len(issues)))
    progress.empty()

    styled = st.session_state.email_style == "Styled"
    html = build_html(greeting, intro, filters_data, footer, fix_version, current_month, styled=styled)
    st.session_state.html_preview = html
    st.session_state.email_content = {
        "html": html, "subject": subject,
        "to": [e.strip() for e in to_emails.split(",") if e.strip()],
        "smtp_user": smtp_user, "smtp_pass": smtp_pass,
    }
    st.success("Report generated! Preview below.")

# ── Preview + Send ────────────────────────────────────────────────────────────
if st.session_state.html_preview:
    st.subheader("📬 Email Preview")
    st.components.v1.html(st.session_state.html_preview, height=720, scrolling=True)
    st.divider()
    col_send, col_dl = st.columns(2)
    with col_send:
        if st.button("📤 Send Email", type="primary", use_container_width=True):
            ec = st.session_state.email_content
            msg = MIMEMultipart()
            msg["From"]    = "PPC Jira Bot <{}>".format(ec["smtp_user"])
            msg["To"]      = ", ".join(ec["to"])
            msg["Subject"] = ec["subject"]
            msg.attach(MIMEText(ec["html"], "html"))
            try:
                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                    server.starttls()
                    server.login(ec["smtp_user"], ec["smtp_pass"])
                    server.send_message(msg)
                st.success("✅ Email sent to: {}".format(", ".join(ec["to"])))
            except Exception as e:
                st.error("❌ Failed to send: {}".format(e))
    with col_dl:
        st.download_button("⬇️ Download HTML", data=st.session_state.html_preview,
                           file_name="qa_report.html", mime="text/html", use_container_width=True)
