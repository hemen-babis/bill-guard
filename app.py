import json
import re
from typing import Dict, Generator, List, Tuple

import streamlit as st

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


APP_TITLE = "BillGuard AI – Understand & Audit Your Medical Bill"
MODEL_NAME = "claude-sonnet-4-6"
SAMPLE_BILL = """Visit: Chest pain evaluation
Facility fee: $1,800
CPT 71045: $800 (chest X-ray)
CPT 93000: $450 (ECG)
Lab panel: $650 ×2
Medication: $300
Total billed: $4,000
Insurance paid: $2,200
Patient responsibility: $1,800"""
SAMPLE_BILL_CLEAN = """Visit: Primary care follow-up
Office visit CPT 99214: $280
Blood pressure check: $40
Total billed: $320
Insurance paid: $220
Patient responsibility: $100"""
SAMPLE_BILL_HIGH_RISK = """Visit: Emergency room abdominal pain evaluation
Facility fee: $2,400
CPT 74176: $3,200 (CT abdomen/pelvis)
Lab panel: $780 ×2
IV hydration: $950
Medication: $425
Total billed: $7,755
Insurance paid: $3,100
Patient responsibility: $4,655"""
SAMPLE_EOB = """Insurance explanation of benefits
Claim status: Processed
Allowed amount: $2,900
Insurance paid: $2,200
Patient responsibility: $700
Notes:
- One lab panel allowed
- Facility fee subject to review
- Patient may be balance billed for non-covered amounts"""

COMPLIANCE_REFS = [
    "HIPAA privacy: Avoid entering real patient identifiers during demos.",
    "EU AI Act transparency: AI-generated guidance should be clearly labeled and reviewable.",
    "Consumer protection: Billing flags are informational, not legal or medical advice.",
]


def extract_money(value: str) -> float:
    cleaned = re.sub(r"[^\d.]", "", value or "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def format_currency(amount: float) -> str:
    return f"${amount:,.0f}"


def parse_bill_input(raw_text: str) -> Dict[str, float]:
    totals = {
        "total_billed": 0.0,
        "insurance_paid": 0.0,
        "patient_responsibility": 0.0,
    }
    for line in raw_text.splitlines():
        lower = line.lower()
        if "total billed" in lower:
            totals["total_billed"] = extract_money(line)
        elif "insurance paid" in lower:
            totals["insurance_paid"] = extract_money(line)
        elif "patient responsibility" in lower or "patient owes" in lower:
            totals["patient_responsibility"] = extract_money(line)
    return totals


def estimate_local_risk(raw_text: str) -> Tuple[float, List[str]]:
    issues = []
    text = raw_text.lower()
    risk = 0.0

    if "lab panel" in text and ("x2" in text or "×2" in text):
        risk += 650.0
        issues.append("Possible duplicate lab panel charge detected.")

    facility_match = re.search(r"facility fee:\s*\$?([\d,]+)", text, re.IGNORECASE)
    if facility_match:
        facility_fee = extract_money(facility_match.group(1))
        if facility_fee >= 1500:
            risk += min(400.0, facility_fee * 0.2)
            issues.append("Facility fee appears unusually high for the listed visit context.")

    billed = parse_bill_input(raw_text).get("total_billed", 0.0)
    if billed >= 3500:
        risk += 150.0
        issues.append("Overall bill size is high enough to justify a manual audit.")

    return risk, issues


def build_prompt(raw_bill: str, insurance_context: str) -> str:
    insurance_block = insurance_context.strip() or "No insurance explanation of benefits was provided."
    return f"""
You are BillGuard AI, an expert medical bill auditor for patients. Your job is to reason carefully, translate billing language into plain English, spot anomalies, and produce actionable dispute guidance.

Analyze the provider bill and any insurance explanation of benefits below. Be skeptical, patient-first, and specific. Compare the two when possible. If something might be legitimate but still worth asking about, say that clearly.

Tasks:
1. Translate every code and item into plain English.
2. Itemize all charges with category, amount, and explanation.
3. Flag possible issues such as duplicates, unusual amounts, inconsistencies, missing context, payer/provider mismatches, or charges that deserve clarification.
4. Break down the finances: total billed, insurance paid, patient owes, and estimated potential overcharge or savings opportunity.
5. Generate a short dispute script the patient can use by phone or secure message.
6. If the insurance explanation of benefits conflicts with the provider bill, call that out clearly.

Return plain text only using this exact section structure and headings:
SUMMARY
- ...

ITEMIZED
- Category | Amount | Explanation

INSURANCE
- Total billed: ...
- Insurance paid: ...
- Patient owes: ...
- Potential overcharge / savings opportunity: ...

FLAGS
- ...

GUIDANCE
- ...

Bill input:
{raw_bill}

Insurance / EOB input:
{insurance_block}
""".strip()


def stream_claude(api_key: str, raw_bill: str, insurance_context: str) -> Generator[str, None, None]:
    if Anthropic is None:
        raise RuntimeError(
            "The `anthropic` package is not installed. Run `pip install anthropic streamlit`."
        )
    client = Anthropic(api_key=api_key)
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=1400,
        temperature=0.2,
        system=(
            "You are a world-class medical billing advocate. "
            "Reason carefully, stay patient-safe, and produce clear structured output."
        ),
        messages=[{"role": "user", "content": build_prompt(raw_bill, insurance_context)}],
    ) as stream:
        for text in stream.text_stream:
            yield text


def stream_chat_response(api_key: str, messages: List[Dict]) -> Generator[str, None, None]:
    client = Anthropic(api_key=api_key)
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=600,
        temperature=0.2,
        system=(
            "You are BillGuard AI, a medical billing advocate helping a patient understand their bill. "
            "Be concise, specific, and patient-friendly. Give actionable next steps when relevant."
        ),
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def parse_structured_sections(raw_output: str) -> Dict[str, List[str]]:
    sections = {
        "SUMMARY": [],
        "ITEMIZED": [],
        "INSURANCE": [],
        "FLAGS": [],
        "GUIDANCE": [],
    }
    current = None

    for line in raw_output.splitlines():
        stripped = line.strip()
        upper = stripped.rstrip(":").upper()
        if upper in sections:
            current = upper
            continue
        if current and stripped:
            sections[current].append(stripped)

    return sections


def parse_itemized_lines(lines: List[str]) -> List[Tuple[str, str, str]]:
    parsed = []
    for line in lines:
        clean = re.sub(r"^[-*]\s*", "", line)
        parts = [part.strip() for part in clean.split("|")]
        if len(parts) >= 3:
            parsed.append((parts[0], parts[1], parts[2]))
        else:
            parsed.append(("Charge", "See note", clean))
    return parsed


def find_money_in_lines(lines: List[str]) -> Dict[str, float]:
    metrics = {
        "total_billed": 0.0,
        "insurance_paid": 0.0,
        "patient_owes": 0.0,
        "potential_savings": 0.0,
    }
    for line in lines:
        lower = line.lower()
        amount = extract_money(line)
        if "total billed" in lower:
            metrics["total_billed"] = amount
        elif "insurance paid" in lower:
            metrics["insurance_paid"] = amount
        elif "patient owes" in lower:
            metrics["patient_owes"] = amount
        elif "potential overcharge" in lower or "savings opportunity" in lower:
            metrics["potential_savings"] = amount
    return metrics


def compute_risk_score(sections: Dict[str, List[str]], local_flags: List[str]) -> int:
    score = 0
    all_flags = sections["FLAGS"] + local_flags
    score += min(len(all_flags) * 15, 60)
    if any("duplicate" in f.lower() for f in all_flags):
        score += 10
    insurance_text = " ".join(sections["INSURANCE"]).lower()
    idx = insurance_text.find("potential")
    if idx != -1:
        savings_match = re.search(r"\$\s*([\d,]+)", insurance_text[idx:])
        if savings_match:
            savings = extract_money(savings_match.group(1))
            if savings > 500:
                score += 15
    return min(score, 95)


def render_risk_badge(score: int) -> None:
    if score >= 60:
        color, label, desc = "#b91c1c", "High Risk", "Multiple billing concerns detected"
    elif score >= 30:
        color, label, desc = "#d97706", "Medium Risk", "Some issues worth reviewing"
    else:
        color, label, desc = "#15803d", "Low Risk", "Bill appears reasonable"
    st.markdown(
        f"""<div style="display:inline-flex;align-items:center;gap:12px;padding:8px 20px;
        border-radius:99px;background:{color};color:white;font-weight:700;
        font-size:0.95rem;margin:8px 0 12px;">
        <span>{label}</span>
        <span style="font-weight:400;font-size:0.85rem;opacity:0.9;">
            Score: {score}/100 &middot; {desc}
        </span></div>""",
        unsafe_allow_html=True,
    )


def render_financial_chart(
    total_billed: float,
    insurance_paid: float,
    patient_owes: float,
    potential_savings: float,
) -> None:
    try:
        import pandas as pd

        data = {
            "Amount ($)": [total_billed, insurance_paid, patient_owes, potential_savings]
        }
        df = pd.DataFrame(
            data,
            index=["Total Billed", "Insurance Paid", "Patient Owes", "Potential Savings"],
        )
        df = df[df["Amount ($)"] > 0]
        if not df.empty:
            st.bar_chart(df, use_container_width=True)
    except ImportError:
        pass


def extract_text_from_upload(uploaded_file) -> Tuple[str, str]:
    file_name = uploaded_file.name.lower()

    if file_name.endswith(".txt"):
        try:
            text = uploaded_file.getvalue().decode("utf-8")
            if not text.strip():
                raise RuntimeError("The text file is empty.")
            return text, "Loaded text file successfully."
        except Exception as exc:
            raise RuntimeError(f"Unable to read text file: {exc}") from exc

    if file_name.endswith(".json"):
        try:
            payload = json.loads(uploaded_file.getvalue().decode("utf-8"))
            return json.dumps(payload, indent=2), "Loaded JSON file successfully."
        except Exception as exc:
            raise RuntimeError(f"Unable to read JSON file: {exc}") from exc

    if file_name.endswith(".pdf"):
        if PdfReader is None:
            raise RuntimeError("PDF support requires `pypdf`. Install it with `pip install pypdf`.")
        try:
            reader = PdfReader(uploaded_file)
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            extracted = "\n".join(page.strip() for page in pages if page.strip())
            if not extracted.strip():
                raise RuntimeError("The PDF opened, but no readable text was found.")
            return extracted, "Loaded PDF bill successfully."
        except Exception as exc:
            raise RuntimeError(f"Unable to read PDF file: {exc}") from exc

    raise RuntimeError("Unsupported file type. Upload a PDF, text, or JSON file.")


def render_header() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🧾", layout="wide")
    st.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(33, 150, 243, 0.13), transparent 30%),
                radial-gradient(circle at top right, rgba(244, 67, 54, 0.12), transparent 28%),
                linear-gradient(180deg, #f6fbff 0%, #eef4f8 100%);
        }
        .hero-card {
            padding: 1.25rem 1.5rem;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(15, 23, 42, 0.08);
            box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
            margin-bottom: 1rem;
        }
        .money-shot {
            padding: 1rem 1.2rem;
            border-radius: 16px;
            background: linear-gradient(135deg, #7f1d1d, #b91c1c);
            color: white;
            font-size: 1.1rem;
            font-weight: 700;
            box-shadow: 0 16px 32px rgba(127, 29, 29, 0.22);
            margin: 0.75rem 0 1rem;
        }
        .micro-note {
            color: #64748b;
            font-size: 0.92rem;
            margin-top: 0.5rem;
            margin-bottom: 0.5rem;
        }
        .section-card {
            padding: 1rem 1.1rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.9);
            border-left: 5px solid #0f766e;
            margin-bottom: 0.85rem;
        }
        .footer-note {
            text-align: center;
            color: #475569;
            font-size: 0.95rem;
            padding-top: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title(APP_TITLE)
    st.markdown(
        """
        <div class="hero-card">
            <strong>From confusion to confidence.</strong> Paste a medical bill, let Claude translate the jargon,
            surface billing risks, estimate what may be contestable, and generate a patient-ready dispute script.
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> str:
    with st.sidebar:
        st.header("Event Setup")
        api_key = st.text_input("Anthropic API Key", type="password", help="Paste the QR code key here at the event.")
        st.caption(f"Model: `{MODEL_NAME}`")
        st.divider()
        st.subheader("Compliance References")
        for ref in COMPLIANCE_REFS:
            st.write(f"- {ref}")
        st.divider()
        st.info("Demo tip: Use the sample bill first for a dramatic live reveal, then paste a second example.")
    return api_key


def render_analysis(raw_output: str, raw_bill: str, insurance_context: str) -> None:
    sections = parse_structured_sections(raw_output)
    metrics = find_money_in_lines(sections["INSURANCE"])
    fallback_totals = parse_bill_input(raw_bill)
    estimated_local_savings, local_flags = estimate_local_risk(raw_bill)

    total_billed = metrics["total_billed"] or fallback_totals["total_billed"]
    insurance_paid = metrics["insurance_paid"] or fallback_totals["insurance_paid"]
    patient_owes = metrics["patient_owes"] or fallback_totals["patient_responsibility"]
    potential_savings = metrics["potential_savings"] or estimated_local_savings

    risk_score = compute_risk_score(sections, local_flags)
    render_risk_badge(risk_score)

    st.markdown(
        f"""
        <div class="money-shot">
            You could save up to {format_currency(potential_savings)} if the flagged issues are corrected.
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Billed", format_currency(total_billed))
    col2.metric("Insurance Paid", format_currency(insurance_paid))
    col3.metric("Patient Owes", format_currency(patient_owes))
    col4.metric("Potential Savings", format_currency(potential_savings))

    render_financial_chart(total_billed, insurance_paid, patient_owes, potential_savings)

    if insurance_context.strip():
        st.subheader("Patient Context")
        st.info("BillGuard compared the provider bill with the uploaded or pasted insurance explanation of benefits.")

    st.subheader("Summary")
    if sections["SUMMARY"]:
        st.success(" ".join(re.sub(r"^[-*]\s*", "", line) for line in sections["SUMMARY"]))
    else:
        st.warning("Claude returned no explicit summary section. Review the raw response below.")

    st.subheader("Itemized Breakdown")
    itemized_rows = parse_itemized_lines(sections["ITEMIZED"])
    if itemized_rows:
        st.table(
            [
                {"Category": c, "Amount": a, "Explanation": e}
                for c, a, e in itemized_rows
            ]
        )
    else:
        st.info("No itemized rows were parsed. Review the raw response below.")

    st.subheader("Financial Impact")
    finance_lines = sections["INSURANCE"] or [
        f"- Total billed: {format_currency(total_billed)}",
        f"- Insurance paid: {format_currency(insurance_paid)}",
        f"- Patient owes: {format_currency(patient_owes)}",
        f"- Potential overcharge / savings opportunity: {format_currency(potential_savings)}",
    ]
    for line in finance_lines:
        st.markdown(f"<span style='color:#0f172a;font-weight:600;'>{line}</span>", unsafe_allow_html=True)
    if potential_savings > 0:
        st.warning(f"Estimated contestable amount: {format_currency(potential_savings)}")

    st.subheader("Red Flags")
    combined_flags = sections["FLAGS"][:]
    if not combined_flags:
        combined_flags = [f"- {flag}" for flag in local_flags]
    else:
        combined_flags.extend([f"- {flag}" for flag in local_flags if flag not in " ".join(combined_flags)])

    if combined_flags:
        for flag in combined_flags:
            clean = re.sub(r"^[-*]\s*", "", flag)
            st.error(f"🚩 {clean}")
    else:
        st.success("No major red flags were identified, but the bill should still be reviewed for payer-specific rules.")

    st.subheader("Ready-to-Use Dispute Guidance")
    if sections["GUIDANCE"]:
        with st.expander("Open dispute script / letter", expanded=True):
            guidance_text = "\n".join(re.sub(r"^[-*]\s*", "", line) for line in sections["GUIDANCE"])
            for line in sections["GUIDANCE"]:
                st.markdown(re.sub(r"^[-*]\s*", "", line))
            st.download_button(
                label="Download Dispute Letter (.txt)",
                data=guidance_text,
                file_name="bill_dispute_letter.txt",
                mime="text/plain",
                use_container_width=True,
            )
    else:
        st.info("No dispute script was parsed. Review the raw response below.")

    with st.expander("Show Claude Raw Response"):
        st.code(raw_output, language="text")


def render_followup_chat(api_key: str, bill_text: str, insurance_text: str, analysis_text: str) -> None:
    st.divider()
    st.subheader("Ask Follow-Up Questions")
    st.caption("Ask Claude anything about your bill — codes, next steps, what to say to your insurer.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_question = st.chat_input("Ask about your bill...")
    if user_question:
        st.session_state.chat_history.append({"role": "user", "content": user_question})
        with st.chat_message("user"):
            st.write(user_question)

        context_msg = (
            f"The patient's medical bill:\n{bill_text}\n\n"
            f"The patient's insurance explanation of benefits:\n{insurance_text or 'None provided'}\n\n"
            f"Previous BillGuard AI analysis:\n{analysis_text}"
        )
        api_messages = [
            {"role": "user", "content": context_msg},
            {"role": "assistant", "content": "I've analyzed your bill and I'm ready to help with any questions."},
        ]
        for msg in st.session_state.chat_history:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        with st.chat_message("assistant"):
            try:
                response_text = st.write_stream(stream_chat_response(api_key, api_messages))
                st.session_state.chat_history.append({"role": "assistant", "content": response_text})
            except Exception as exc:
                st.error(f"Could not get response: {exc}")


def render_fallback_analysis(raw_bill: str) -> None:
    estimated_savings, fallback_flags = estimate_local_risk(raw_bill)
    fallback_totals = parse_bill_input(raw_bill)

    st.markdown(
        f"""
        <div class="money-shot">
            Fallback audit: potential savings opportunity of {format_currency(estimated_savings)} based on local pattern checks.
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Billed", format_currency(fallback_totals["total_billed"]))
    col2.metric("Insurance Paid", format_currency(fallback_totals["insurance_paid"]))
    col3.metric("Patient Owes", format_currency(fallback_totals["patient_responsibility"]))
    col4.metric("Potential Savings", format_currency(estimated_savings))

    st.warning("API connection issue. This is common on event Wi-Fi. Try re-entering your key or ask an organizer with an orange badge.")
    st.subheader("Local Risk Flags")
    if fallback_flags:
        for flag in fallback_flags:
            st.error(f"🚩 {flag}")
    else:
        st.info("No obvious risks were detected locally, but Claude analysis is recommended for deeper review.")

    st.subheader("Immediate Patient Script")
    st.warning(
        "Hello, I reviewed this bill and I need an itemized explanation of each charge, especially any repeated lab fees and the facility fee. "
        "Please confirm whether any charges were duplicated, explain how my patient responsibility was calculated, and send a corrected statement if errors are found."
    )


def main() -> None:
    render_header()
    api_key = render_sidebar()

    if "bill_text" not in st.session_state:
        st.session_state.bill_text = SAMPLE_BILL
    if "insurance_text" not in st.session_state:
        st.session_state.insurance_text = SAMPLE_EOB
    if "last_analysis" not in st.session_state:
        st.session_state.last_analysis = ""
    if "last_bill" not in st.session_state:
        st.session_state.last_bill = ""
    if "last_insurance" not in st.session_state:
        st.session_state.last_insurance = ""
    if "analysis_ready" not in st.session_state:
        st.session_state.analysis_ready = False
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    st.subheader("Upload a Bill or Paste Text")
    sample_col1, sample_col2, sample_col3 = st.columns(3)
    if sample_col1.button("Load Demo Bill", use_container_width=True):
        st.session_state.bill_text = SAMPLE_BILL
        st.session_state.insurance_text = SAMPLE_EOB
        st.session_state.analysis_ready = False
        st.session_state.chat_history = []
    if sample_col2.button("Load Clean Bill", use_container_width=True):
        st.session_state.bill_text = SAMPLE_BILL_CLEAN
        st.session_state.insurance_text = "Insurance explanation of benefits\nClaim status: Processed\nAllowed amount: $280\nInsurance paid: $220\nPatient responsibility: $100"
        st.session_state.analysis_ready = False
        st.session_state.chat_history = []
    if sample_col3.button("Load High-Risk Bill", use_container_width=True):
        st.session_state.bill_text = SAMPLE_BILL_HIGH_RISK
        st.session_state.insurance_text = """Insurance explanation of benefits
Claim status: Processed
Allowed amount: $3,800
Insurance paid: $3,100
Patient responsibility: $700
Notes:
- CT scan allowed
- Only one lab panel allowed
- IV hydration requires itemized review"""
        st.session_state.analysis_ready = False
        st.session_state.chat_history = []

    upload_col1, upload_col2 = st.columns(2)
    uploaded_bill_file = upload_col1.file_uploader(
        "Upload provider bill",
        type=["pdf", "json", "txt"],
        help="Upload the provider bill as a PDF, plain text file, or JSON export.",
        key="provider_upload",
    )
    uploaded_insurance_file = upload_col2.file_uploader(
        "Upload insurance EOB",
        type=["pdf", "json", "txt"],
        help="Upload an explanation of benefits or insurer summary to compare against the bill.",
        key="insurance_upload",
    )
    if uploaded_bill_file is not None:
        try:
            uploaded_text, upload_message = extract_text_from_upload(uploaded_bill_file)
            st.session_state.bill_text = uploaded_text
            st.session_state.analysis_ready = False
            st.session_state.chat_history = []
            st.success(f"Provider bill: {upload_message}")
        except Exception as exc:
            st.error(str(exc))
    if uploaded_insurance_file is not None:
        try:
            uploaded_text, upload_message = extract_text_from_upload(uploaded_insurance_file)
            st.session_state.insurance_text = uploaded_text
            st.session_state.analysis_ready = False
            st.session_state.chat_history = []
            st.success(f"Insurance EOB: {upload_message}")
        except Exception as exc:
            st.error(str(exc))

    input_col1, input_col2 = st.columns(2)
    bill_text = input_col1.text_area(
        "Provider bill text / extracted upload",
        key="bill_text",
        height=260,
        help="Paste provider bill text directly, or upload a PDF/JSON/text file above to auto-fill this field.",
    )
    insurance_text = input_col2.text_area(
        "Insurance EOB / portal text",
        key="insurance_text",
        height=260,
        help="Paste explanation-of-benefits text, denied-claim notes, or insurer portal text here.",
    )
    st.markdown(
        """
        <div class="micro-note">
            Informational only. Not medical or legal advice. Always verify with your provider or insurer.
        </div>
        """,
        unsafe_allow_html=True,
    )

    analyze = st.button("Analyze My Bill", type="primary", use_container_width=True)

    if analyze:
        if not bill_text.strip():
            st.warning("Paste bill text or JSON before running the analysis.")
            return
        if not api_key.strip():
            st.warning("Enter an Anthropic API key in the sidebar to run Claude.")
            return

        st.session_state.analysis_ready = False
        st.session_state.chat_history = []

        try:
            with st.status("Claude is auditing your bill...", expanded=True) as status:
                st.write("Reading provider charges and insurance decisions...")
                full_text = st.write_stream(stream_claude(api_key, bill_text, insurance_text))
                status.update(label="Analysis complete!", state="complete", expanded=False)

            if not full_text:
                raise RuntimeError("Claude returned an empty response.")

            st.session_state.last_analysis = full_text
            st.session_state.last_bill = bill_text
            st.session_state.last_insurance = insurance_text
            st.session_state.analysis_ready = True
            st.success("Analysis complete! You may be able to save money. Review the flags and dispute script below.")
            render_analysis(full_text, bill_text, insurance_text)

        except Exception as exc:
            st.error("Claude analysis did not complete.")
            st.exception(exc)
            render_fallback_analysis(bill_text)

    elif st.session_state.analysis_ready and st.session_state.last_analysis:
        render_analysis(
            st.session_state.last_analysis,
            st.session_state.last_bill,
            st.session_state.last_insurance,
        )

    if st.session_state.analysis_ready and api_key.strip():
        render_followup_chat(
            api_key,
            st.session_state.last_bill,
            st.session_state.last_insurance,
            st.session_state.last_analysis,
        )

    st.markdown(
        """
        <div class="footer-note">
            PDX Hacks 2026 • Built in 3 hours • Powered by Claude • Patient-first transparency • Potential to save patients thousands
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
