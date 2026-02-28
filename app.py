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


APP_TITLE = "BillGuard AI"
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

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

html, body, .stApp, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

.block-container {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    max-width: 100% !important;
}

[data-testid="stAppViewContainer"] > .main {
    padding-top: 0 !important;
}

/* ── Background ─────────────────────────────────────────── */
.stApp {
    background: #f0f4ff;
    background-image:
        radial-gradient(ellipse 80% 50% at 10% 0%, rgba(99,102,241,0.08) 0%, transparent 55%),
        radial-gradient(ellipse 60% 40% at 90% 5%, rgba(6,182,212,0.07) 0%, transparent 50%);
    min-height: 100vh;
}

/* ── Hide Streamlit chrome ───────────────────────────────── */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }


/* ── Section headers ─────────────────────────────────────── */
.sh {
    display: flex; align-items: center; gap: 10px;
    margin: 2rem 0 0.9rem;
    padding-bottom: 0.7rem;
    border-bottom: 2px solid rgba(99,102,241,0.1);
}
.sh-icon {
    width: 33px; height: 33px; border-radius: 8px;
    display: inline-flex; align-items: center;
    justify-content: center; font-size: 0.95rem; flex-shrink: 0;
}
.sh-title { font-size: 1.05rem; font-weight: 700; color: #0f172a; margin: 0; }

/* ── Metrics grid ────────────────────────────────────────── */
.metrics-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 1rem; margin: 1.25rem 0;
}
.mc {
    background: white; border-radius: 14px;
    padding: 1.15rem 1.35rem;
    border: 1px solid rgba(15,23,42,0.07);
    box-shadow: 0 1px 2px rgba(0,0,0,0.03), 0 4px 14px rgba(0,0,0,0.05);
    display: flex; flex-direction: column; gap: 5px;
    transition: transform 0.15s, box-shadow 0.15s;
}
.mc:hover {
    transform: translateY(-2px);
    box-shadow: 0 2px 4px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.08);
}
.mc.savings {
    background: linear-gradient(135deg, #fef2f2 0%, #fff7ed 100%);
    border-color: rgba(239,68,68,0.14);
}
.mc-label {
    font-size: 0.69rem; font-weight: 700; color: #94a3b8;
    text-transform: uppercase; letter-spacing: 0.9px;
}
.mc-value {
    font-size: 1.7rem; font-weight: 800; color: #0f172a;
    letter-spacing: -0.5px; line-height: 1;
}
.mc.savings .mc-value { color: #dc2626; }

/* ── Risk banner ─────────────────────────────────────────── */
.risk-banner {
    border-radius: 16px; padding: 1.1rem 1.6rem;
    display: flex; align-items: center; justify-content: space-between;
    margin: 1.25rem 0; gap: 1rem;
}
.risk-banner.high  { background: linear-gradient(135deg, #7f1d1d 0%, #991b1b 100%); }
.risk-banner.medium { background: linear-gradient(135deg, #78350f 0%, #b45309 100%); }
.risk-banner.low   { background: linear-gradient(135deg, #14532d 0%, #166534 100%); }
.risk-left { display: flex; flex-direction: column; gap: 3px; }
.risk-label { font-size: 1.1rem; font-weight: 800; color: white; letter-spacing: -0.3px; }
.risk-desc  { font-size: 0.83rem; color: rgba(255,255,255,0.62); }
.risk-right { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.risk-score-num {
    font-size: 2.5rem; font-weight: 900; color: white;
    line-height: 1; letter-spacing: -1px;
}
.risk-score-lbl {
    font-size: 0.68rem; color: rgba(255,255,255,0.45);
    text-transform: uppercase; letter-spacing: 1px;
}

/* ── Savings callout ─────────────────────────────────────── */
.savings-callout {
    background: linear-gradient(135deg, #1e3a5f 0%, #1d4ed8 100%);
    border-radius: 16px; padding: 1.2rem 1.75rem;
    display: flex; align-items: center; justify-content: space-between;
    margin: 0.25rem 0 1.25rem; gap: 1rem;
    box-shadow: 0 8px 24px rgba(29,78,216,0.22);
}
.sc-left { display: flex; flex-direction: column; gap: 3px; }
.sc-eyebrow {
    font-size: 0.7rem; font-weight: 700; color: rgba(255,255,255,0.5);
    text-transform: uppercase; letter-spacing: 1px;
}
.sc-amount {
    font-size: 2.2rem; font-weight: 900; color: white;
    line-height: 1; letter-spacing: -1px;
}
.sc-note { font-size: 0.8rem; color: rgba(255,255,255,0.55); }
.sc-right { font-size: 2.5rem; opacity: 0.22; }

/* ── EOB context badge ───────────────────────────────────── */
.eob-badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: linear-gradient(135deg, #eff6ff, #e0f2fe);
    border: 1px solid rgba(14,165,233,0.25);
    border-radius: 10px; padding: 0.65rem 1.1rem;
    color: #0369a1; font-size: 0.85rem; font-weight: 600;
    margin: 0.75rem 0 1.25rem;
}

/* ── Summary card ────────────────────────────────────────── */
.summary-card {
    background: linear-gradient(135deg, #f8fffb 0%, #eefbf3 100%);
    border: 1px solid rgba(16,185,129,0.16);
    border-radius: 18px;
    padding: 1.2rem 1.35rem 1.1rem;
    color: #14532d;
    box-shadow: 0 10px 24px rgba(16, 185, 129, 0.08);
}
.summary-lead {
    font-size: 1.05rem;
    line-height: 1.65;
    font-weight: 700;
    color: #1f5138;
    margin-bottom: 0.9rem;
    font-family: 'Inter', sans-serif;
}
.summary-points {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
}
.summary-point {
    display: flex;
    align-items: flex-start;
    gap: 0.65rem;
    font-size: 0.95rem;
    line-height: 1.6;
    color: #355b48;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
}
.summary-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: #22c55e;
    margin-top: 0.48rem;
    flex-shrink: 0;
}

/* ── Itemized table ──────────────────────────────────────── */
.itbl {
    background: white; border-radius: 14px; overflow: hidden;
    border: 1px solid rgba(15,23,42,0.07);
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04);
    margin-bottom: 1rem; width: 100%;
}
.itbl table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
.itbl thead tr { background: linear-gradient(135deg, #f8fafc, #f1f5f9); }
.itbl th {
    padding: 0.8rem 1.1rem; text-align: left;
    font-weight: 700; color: #64748b;
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.8px;
    border-bottom: 2px solid rgba(15,23,42,0.08);
}
.itbl td {
    padding: 0.8rem 1.1rem;
    border-bottom: 1px solid rgba(15,23,42,0.05);
    color: #334155; vertical-align: top; line-height: 1.45;
}
.itbl tr:last-child td { border-bottom: none; }
.itbl tr:nth-child(even) td { background: rgba(248,250,252,0.7); }
.itbl .td-cat { font-weight: 600; color: #0f172a; white-space: nowrap; }
.itbl .td-amt { font-weight: 700; color: #6366f1; white-space: nowrap; }

/* ── Finance rows ────────────────────────────────────────── */
.finance-wrap {
    background: white; border-radius: 14px;
    padding: 0.35rem 1.25rem;
    border: 1px solid rgba(15,23,42,0.07);
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04);
    margin-bottom: 1rem;
}
.finance-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.7rem 0; border-bottom: 1px solid rgba(15,23,42,0.06);
    font-size: 0.9rem;
}
.finance-row:last-child { border-bottom: none; }
.fkey { color: #475569; font-weight: 500; }
.fval { color: #0f172a; font-weight: 700; }
.fval.hot { color: #dc2626; }

/* ── Flag cards ──────────────────────────────────────────── */
.flag-card {
    display: flex; align-items: flex-start; gap: 14px;
    padding: 1.15rem 1.25rem; border-radius: 16px;
    margin-bottom: 0.55rem; border-left: 5px solid;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
}
.flag-card.err  { background: linear-gradient(135deg, #fff7f7 0%, #fff1f1 100%); border-color: #ef4444; }
.flag-card.warn { background: linear-gradient(135deg, #fffdf5 0%, #fff8e7 100%); border-color: #f59e0b; }
.flag-card.ok   { background: #f0fdf4; border-left: 4px solid #10b981; }
.fi { font-size: 1rem; flex-shrink: 0; line-height: 1.4; margin-top: 4px; }
.flag-copy { display: flex; flex-direction: column; gap: 0.35rem; width: 100%; }
.flag-topline { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flag-pill {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 4px 11px; border-radius: 999px; font-size: 0.7rem;
    font-weight: 800; letter-spacing: 0.8px; text-transform: uppercase;
    font-family: 'Inter', sans-serif;
}
.flag-pill.critical, .flag-pill.important, .flag-pill.significant {
    background: #fee2e2; color: #b91c1c;
}
.flag-pill.moderate, .flag-pill.low, .flag-pill.worth {
    background: #fef3c7; color: #b45309;
}
.flag-title {
    font-size: 1.12rem; color: #111827; line-height: 1.35; font-weight: 800;
    font-family: 'Inter', sans-serif;
}
.flag-subtext {
    font-size: 0.92rem; color: #5b6475; line-height: 1.55; font-weight: 500;
    font-family: 'Inter', sans-serif;
}
.flag-detail {
    font-size: 1rem;
    line-height: 1.65;
    color: #1f2937;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
}

/* ── Guidance card ───────────────────────────────────────── */
.guidance-card {
    background: linear-gradient(135deg, #fafbff 0%, #f5f3ff 100%);
    border: 1px solid rgba(99,102,241,0.14);
    border-radius: 14px; padding: 1.25rem 1.5rem;
    color: #1e293b; line-height: 1.7; font-size: 0.9rem;
}
.guidance-card p { margin: 0 0 0.5rem; }
.guidance-card p:last-child { margin-bottom: 0; }

/* ── Divider ─────────────────────────────────────────────── */
.cdiv { height: 1px; background: rgba(15,23,42,0.07); margin: 2rem 0; border-radius: 99px; }

/* ── Micro note ──────────────────────────────────────────── */
.micro-note { color: #94a3b8; font-size: 0.79rem; margin-top: 0.35rem; margin-bottom: 0.65rem; }

/* ── Landing page ────────────────────────────────────────── */
.lp-hero {
    background:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80'%3E%3Crect x='34' y='22' width='12' height='36' rx='3' fill='rgba(255,255,255,0.018)'/%3E%3Crect x='22' y='34' width='36' height='12' rx='3' fill='rgba(255,255,255,0.018)'/%3E%3C/svg%3E"),
        linear-gradient(160deg, #050d1a 0%, #0b1e3d 45%, #07111f 100%);
    border-radius: 0;
    padding: 5rem 3rem 4rem;
    text-align: center;
    position: relative;
    overflow: hidden;
    border: none;
    box-shadow: none;
    margin-bottom: 1.5rem;
    margin-top: 0;
    width: 100vw;
    margin-left: calc(50% - 50vw);
    margin-right: calc(50% - 50vw);
    min-height: min(92vh, 980px);
    display: flex;
    flex-direction: column;
    justify-content: center;
}
.lp-g1 {
    position: absolute; top: -140px; right: -80px;
    width: 600px; height: 600px;
    background: radial-gradient(circle, rgba(6,182,212,0.13) 0%, transparent 60%);
    pointer-events: none;
}
.lp-g2 {
    position: absolute; bottom: -100px; left: -80px;
    width: 500px; height: 500px;
    background: radial-gradient(circle, rgba(99,102,241,0.12) 0%, transparent 60%);
    pointer-events: none;
}
.lp-g3 {
    position: absolute; top: 20%; left: 50%; transform: translateX(-50%);
    width: 700px; height: 350px;
    background: radial-gradient(ellipse, rgba(6,182,212,0.05) 0%, transparent 65%);
    pointer-events: none;
}
.lp-badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(6,182,212,0.1);
    border: 1px solid rgba(6,182,212,0.22);
    color: #67e8f9;
    padding: 6px 18px; border-radius: 99px;
    font-size: 0.72rem; font-weight: 700;
    letter-spacing: 1.2px; text-transform: uppercase;
    margin-bottom: 1.75rem;
}
.lp-brand {
    font-size: clamp(2rem, 4vw, 3.2rem);
    font-weight: 900;
    color: white;
    letter-spacing: -1.4px;
    margin-bottom: 2rem;
    line-height: 1;
}
.lp-title {
    font-size: clamp(3rem, 8vw, 6.25rem); font-weight: 900;
    color: white; margin: 0 0 1rem;
    line-height: 0.98; letter-spacing: -3px;
}
.lp-accent {
    background: linear-gradient(135deg, #22d3ee 0%, #a5b4fc 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
}
.lp-subtitle {
    font-size: clamp(1rem, 1.7vw, 1.18rem); color: #94a3b8;
    margin: 0 auto 2.25rem; max-width: 720px; line-height: 1.75;
}
.lp-ecg {
    display: block; width: 100%; height: 52px;
    margin: 0.25rem 0 2.5rem;
}
.lp-stats {
    display: flex; align-items: center;
    justify-content: center; gap: 0; margin-bottom: 3rem;
}
.lp-stat { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 0 2.75rem; }
.lp-stat-div { width: 1px; height: 40px; background: rgba(255,255,255,0.1); flex-shrink: 0; }
.lp-stat-n { font-size: 1.85rem; font-weight: 800; color: white; line-height: 1; letter-spacing: -1px; }
.lp-stat-l { font-size: 0.68rem; color: #4b5f7c; text-transform: uppercase; letter-spacing: 0.9px; }

@media (max-width: 900px) {
    .lp-hero {
        padding: 3.5rem 1.25rem 3rem;
        min-height: auto;
    }
    .lp-title {
        letter-spacing: -2px;
    }
    .lp-stats {
        flex-wrap: wrap;
        gap: 1.25rem;
    }
    .lp-stat {
        padding: 0 1rem;
    }
    .lp-stat-div {
        display: none;
    }
    .lp-care-grid,
    .lp-why-grid,
    .metrics-grid {
        grid-template-columns: 1fr !important;
    }
}

/* Section label + title */
.lp-sec-label {
    text-align: center; font-size: 0.7rem; font-weight: 700;
    color: #94a3b8; text-transform: uppercase; letter-spacing: 1.8px;
    margin: 2.5rem 0 0.6rem;
}
.lp-sec-title {
    text-align: center; font-size: 1.85rem; font-weight: 800;
    color: #0f172a; letter-spacing: -0.75px; margin-bottom: 1.5rem; line-height: 1.2;
}

/* CARE cards — reimagined with medical icons */
.lp-care-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 1rem; margin-bottom: 0.5rem;
}
.lp-care-card {
    background: white; border-radius: 18px;
    padding: 1.75rem 1.5rem 1.5rem;
    border: 1px solid rgba(15,23,42,0.07);
    box-shadow: 0 1px 3px rgba(0,0,0,0.03), 0 8px 22px rgba(0,0,0,0.06);
    display: flex; flex-direction: column; gap: 8px;
    position: relative; overflow: hidden;
    transition: transform 0.2s, box-shadow 0.2s;
}
.lp-care-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 4px 8px rgba(0,0,0,0.04), 0 16px 36px rgba(0,0,0,0.1);
}
.lp-care-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 3px; border-radius: 18px 18px 0 0;
    background: linear-gradient(90deg, #6366f1, #06b6d4);
}
.lp-care-icon { font-size: 1.75rem; line-height: 1; margin-bottom: 2px; }
.lp-care-step {
    font-size: 2rem; font-weight: 900; letter-spacing: -1px;
    text-transform: uppercase; color: #0f172a; line-height: 1;
    margin-bottom: 2px;
}
.lp-care-word {
    font-size: 0.72rem; font-weight: 800; letter-spacing: 1.6px;
    text-transform: uppercase; color: #6366f1; margin-bottom: 4px;
}
.lp-care-title { font-size: 1.08rem; font-weight: 800; color: #0f172a; }
.lp-care-desc { font-size: 0.82rem; color: #64748b; line-height: 1.58; }

/* Why section — dark medical feel */
.lp-why {
    background:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='60' height='60'%3E%3Ccircle cx='30' cy='30' r='1.5' fill='rgba(255,255,255,0.04)'/%3E%3C/svg%3E"),
        linear-gradient(135deg, #061122 0%, #0d2144 50%, #051020 100%);
    border-radius: 22px;
    padding: 3.5rem 2.5rem 3rem;
    text-align: center;
    margin: 1.5rem 0;
    border: 1px solid rgba(255,255,255,0.05);
    box-shadow: 0 24px 60px rgba(6,17,34,0.4);
}
.lp-why-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 1.25rem; margin-top: 1.75rem;
}
.lp-why-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px; padding: 2rem 1.5rem;
    transition: background 0.2s;
}
.lp-why-card:hover { background: rgba(255,255,255,0.07); }
.lp-why-icon { font-size: 1.6rem; margin-bottom: 0.85rem; display: block; }
.lp-why-num {
    font-size: 2.2rem; font-weight: 900; color: white;
    letter-spacing: -1px; line-height: 1; margin-bottom: 0.5rem;
}
.lp-why-lbl { font-size: 0.84rem; color: rgba(255,255,255,0.5); line-height: 1.55; }

/* Trust strip */
.lp-trust {
    display: flex; align-items: center;
    justify-content: center; gap: 1.25rem;
    padding: 1.5rem 1rem; flex-wrap: wrap; margin: 1rem 0;
}
.lp-trust-item {
    display: flex; align-items: center; gap: 6px;
    font-size: 0.82rem; color: #475569; font-weight: 600;
}
.lp-trust-sep { color: #e2e8f0; font-size: 1.1rem; }

/* Bottom CTA block */
.lp-cta-block {
    background:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80'%3E%3Crect x='34' y='22' width='12' height='36' rx='3' fill='rgba(255,255,255,0.025)'/%3E%3Crect x='22' y='34' width='36' height='12' rx='3' fill='rgba(255,255,255,0.025)'/%3E%3C/svg%3E"),
        linear-gradient(135deg, #0a1628 0%, #16316a 50%, #0a1628 100%);
    border-radius: 22px;
    padding: 3.5rem 2.5rem 3rem;
    text-align: center; margin: 0.5rem 0 1rem;
    border: 1px solid rgba(255,255,255,0.07);
    box-shadow: 0 24px 60px rgba(10,22,40,0.4);
    position: relative; overflow: hidden;
}
.lp-cta-block::before {
    content: '';
    position: absolute; top: -100px; left: 50%; transform: translateX(-50%);
    width: 500px; height: 300px;
    background: radial-gradient(ellipse, rgba(6,182,212,0.1) 0%, transparent 65%);
    pointer-events: none;
}
.lp-cta-eyebrow {
    font-size: 0.7rem; font-weight: 700; color: rgba(255,255,255,0.35);
    text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 0.75rem;
}
.lp-cta-title {
    font-size: 2.2rem; font-weight: 900; color: white;
    letter-spacing: -1px; line-height: 1.1; margin-bottom: 0.6rem;
}
.lp-cta-sub { font-size: 0.92rem; color: rgba(255,255,255,0.45); margin-bottom: 2rem; }


/* ── Footer ──────────────────────────────────────────────── */
.app-footer {
    text-align: center; color: #94a3b8; font-size: 0.8rem;
    padding: 2rem 0 0.75rem;
    border-top: 1px solid rgba(15,23,42,0.07);
    margin-top: 2.5rem; line-height: 1.6;
}

/* ── Streamlit widget overrides ──────────────────────────── */
[data-testid="stTextArea"] textarea {
    border-radius: 12px !important;
    border: 2px solid rgba(15,23,42,0.09) !important;
    background: white !important;
    font-size: 0.84rem !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
    padding: 0.8rem 1rem !important;
}
[data-testid="stTextArea"] textarea:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 3px rgba(99,102,241,0.09) !important;
    outline: none !important;
}
[data-testid="stTextArea"] textarea::placeholder { color: #cbd5e1 !important; }

[data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
    border: none !important; border-radius: 12px !important;
    font-weight: 700 !important; font-size: 0.96rem !important;
    letter-spacing: 0.2px !important; color: white !important;
    box-shadow: 0 4px 16px rgba(99,102,241,0.3) !important;
    transition: all 0.2s !important;
    padding: 0.65rem 1.5rem !important;
}
[data-testid="stButton"] button[kind="primary"]:hover {
    box-shadow: 0 6px 22px rgba(99,102,241,0.42) !important;
    filter: brightness(1.06) !important;
}

[data-testid="stButton"] button:not([kind="primary"]) {
    border-radius: 10px !important; font-weight: 600 !important;
    font-size: 0.85rem !important;
    border: 1.5px solid rgba(15,23,42,0.1) !important;
    background: white !important; color: #334155 !important;
    transition: all 0.15s !important;
}
[data-testid="stButton"] button:not([kind="primary"]):hover {
    border-color: #6366f1 !important; color: #6366f1 !important;
    background: rgba(99,102,241,0.04) !important;
}

[data-testid="stDownloadButton"] button {
    background: rgba(99,102,241,0.06) !important;
    border: 1.5px solid rgba(99,102,241,0.28) !important;
    color: #6366f1 !important; border-radius: 10px !important;
    font-weight: 600 !important; transition: all 0.2s !important;
}
[data-testid="stDownloadButton"] button:hover {
    background: #6366f1 !important; color: white !important;
    border-color: #6366f1 !important;
}

[data-testid="stFileUploaderDropzone"] {
    border: 2px dashed rgba(99,102,241,0.22) !important;
    border-radius: 14px !important; background: white !important;
    transition: border-color 0.2s !important;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: rgba(99,102,241,0.42) !important;
    background: rgba(99,102,241,0.02) !important;
}

.stSidebar [data-testid="stSidebarContent"] { background: white !important; }

[data-testid="stExpander"] {
    border-radius: 13px !important; overflow: hidden !important;
    border: 1px solid rgba(15,23,42,0.08) !important;
    background: white !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03) !important;
}

[data-testid="stStatus"] {
    border-radius: 14px !important;
    border: 1px solid rgba(99,102,241,0.14) !important;
    background: rgba(99,102,241,0.02) !important;
}

[data-testid="stAlert"] { border-radius: 12px !important; }

::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(15,23,42,0.1); border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: rgba(15,23,42,0.2); }
</style>
"""


# ─────────────────────────── core helpers ────────────────────────────

def extract_money(value: str) -> float:
    if not value:
        return 0.0
    match = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", value)
    if not match:
        return 0.0
    cleaned = match.group(1).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def format_currency(amount: float) -> str:
    return f"${amount:,.0f}"


def parse_bill_input(raw_text: str) -> Dict[str, float]:
    totals = {"total_billed": 0.0, "insurance_paid": 0.0, "patient_responsibility": 0.0}
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
5. Generate a formal dispute letter that is specific to this exact case, using the facts from the bill and EOB.
6. Generate a short phone script the patient can use when calling the provider or insurer.
7. Generate a short action plan with concrete next steps in the right order.
8. If the insurance explanation of benefits conflicts with the provider bill, call that out clearly.

Important judgment rules:
- Do not overstate risk. If the bill is mostly reasonable and only has one or two clarification questions, say so.
- Separate severe issues from low-severity "worth asking about" items.
- Use severity labels inside FLAGS such as CRITICAL, IMPORTANT, MODERATE, or LOW.
- Always include GUIDANCE, even if the guidance is simply to call and ask for clarification rather than dispute aggressively.
- Always include DISPUTE_LETTER.
- Always include ACTION_PLAN.
- DISPUTE_LETTER must read like a real ready-to-send letter, not a checklist, numbered plan, or notes.
- DISPUTE_LETTER must be tailored to the uploaded case. Reference actual conflicts, dates, totals, review status, and request an itemized bill, payment hold, and written response when supported by the inputs.
- If patient or provider names are missing, use bracketed placeholders such as [Patient Name] or [Provider Name].
- Keep DISPUTE_LETTER professional and concise.
- ACTION_PLAN should be a numbered sequence of practical next steps. It is separate from the formal letter.
- PHONE_SCRIPT should be brief and speakable.
- If the overall bill appears mostly clean, say that in SUMMARY.

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

ACTION_PLAN
1. ...

DISPUTE_LETTER
[Date]
[Provider Billing Department]
...

PHONE_SCRIPT
- ...

Bill input:
{raw_bill}

Insurance / EOB input:
{insurance_block}
""".strip()


def stream_claude(api_key: str, raw_bill: str, insurance_context: str) -> Generator[str, None, None]:
    if Anthropic is None:
        raise RuntimeError("The `anthropic` package is not installed. Run `pip install anthropic streamlit`.")
    client = Anthropic(api_key=api_key)
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=2200,
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
        "ACTION_PLAN": [],
        "DISPUTE_LETTER": [],
        "PHONE_SCRIPT": [],
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
    metrics = {"total_billed": 0.0, "insurance_paid": 0.0, "patient_owes": 0.0, "potential_savings": 0.0}
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

    for flag in sections["FLAGS"]:
        lower = flag.lower()
        if "critical" in lower:
            score += 22
        elif "important" in lower or "significant" in lower:
            score += 14
        elif "moderate" in lower:
            score += 8
        elif "low" in lower or "worth asking" in lower:
            score += 4
        else:
            score += 6

    score += min(len(local_flags) * 8, 16)

    if any("duplicate" in f.lower() for f in all_flags):
        score += 8
    if any("balance bill" in f.lower() or "denied" in f.lower() for f in all_flags):
        score += 6

    insurance_text = " ".join(sections["INSURANCE"]).lower()
    idx = insurance_text.find("potential")
    if idx != -1:
        m = re.search(r"\$\s*([\d,]+)", insurance_text[idx:])
        if m:
            savings = extract_money(m.group(1))
            if savings > 1000:
                score += 15
            elif savings > 250:
                score += 8
            elif savings > 0:
                score += 3

    summary_text = " ".join(sections["SUMMARY"]).lower()
    if "mostly clean" in summary_text or "mostly reasonable" in summary_text:
        score -= 12

    return max(5, min(score, 95))


def summarize_flag(flag_text: str) -> str:
    text = re.sub(r"^[-*]\s*", "", flag_text).strip()
    text = re.sub(r"^flag\s*\d+\s*[—|-]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\[[A-Z\s]+\]\s*", "", text)
    text = re.sub(r"^(critical|important|significant|moderate|low|worth asking)\s*[|—:-]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    if ":" in text:
        title = text.split(":", 1)[0].strip()
    else:
        title = re.split(r"(?<=[.!?])\s+", text)[0].strip()

    title = re.sub(r"^\[[A-Z\s]+\]\s*", "", title).strip(" -")
    if len(title.split()) > 9:
        title = " ".join(title.split()[:9]) + "..."

    return title or "Billing issue needs review"


def flag_severity(flag_text: str) -> str:
    lower = flag_text.lower()
    for label in ["critical", "important", "significant", "moderate", "low", "worth asking"]:
        if label in lower:
            return label
    return "review"


def concise_flag_detail(flag_text: str) -> str:
    text = re.sub(r"^[-*]\s*", "", flag_text).strip()
    if ":" in text:
        detail = text.split(":", 1)[1].strip()
    else:
        detail = text
    detail = re.sub(r"^\[[A-Z\s]+\]\s*", "", detail).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", detail)[0].strip()
    if not first_sentence:
        first_sentence = detail
    first_sentence = re.sub(r"\s+", " ", first_sentence)
    words = first_sentence.split()
    if len(words) > 18:
        first_sentence = " ".join(words[:18]) + "..."
    return first_sentence


def short_flag_explainer(flag_text: str) -> str:
    text = re.sub(r"^[-*]\s*", "", flag_text).strip()
    if ":" in text:
        detail = text.split(":", 1)[1].strip()
    else:
        detail = text

    sentences = re.split(r"(?<=[.!?])\s+", detail)
    compact = " ".join(sentence.strip() for sentence in sentences[:2] if sentence.strip())
    compact = re.sub(r"\s+", " ", compact).strip()

    compact = re.sub(r"^\[[A-Z\s]+\]\s*", "", compact).strip()
    words = compact.split()
    if len(words) > 34:
        compact = " ".join(words[:34]) + "..."
    return compact


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
            raise RuntimeError("PDF support requires `pypdf`. Run `pip install pypdf`.")
        try:
            reader = PdfReader(uploaded_file)
            pages = [page.extract_text() or "" for page in reader.pages]
            extracted = "\n".join(p.strip() for p in pages if p.strip())
            if not extracted.strip():
                raise RuntimeError("The PDF opened, but no readable text was found.")
            return extracted, "Loaded PDF bill successfully."
        except Exception as exc:
            raise RuntimeError(f"Unable to read PDF file: {exc}") from exc
    raise RuntimeError("Unsupported file type. Upload a PDF, .txt, or JSON file.")


# ─────────────────────────── UI helpers ──────────────────────────────

def section_header(icon: str, title: str, bg: str = "#ede9fe", color: str = "#4f46e5") -> None:
    st.markdown(
        f'<div class="sh">'
        f'<span class="sh-icon" style="background:{bg};color:{color}">{icon}</span>'
        f'<span class="sh-title">{title}</span></div>',
        unsafe_allow_html=True,
    )


def render_metrics(total_billed: float, insurance_paid: float,
                   patient_owes: float, potential_savings: float) -> None:
    st.markdown(
        f"""<div class="metrics-grid">
            <div class="mc">
                <div class="mc-label">Total Billed</div>
                <div class="mc-value">{format_currency(total_billed)}</div>
            </div>
            <div class="mc">
                <div class="mc-label">Insurance Paid</div>
                <div class="mc-value">{format_currency(insurance_paid)}</div>
            </div>
            <div class="mc">
                <div class="mc-label">Patient Owes</div>
                <div class="mc-value">{format_currency(patient_owes)}</div>
            </div>
            <div class="mc savings">
                <div class="mc-label">Potential Savings</div>
                <div class="mc-value">{format_currency(potential_savings)}</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_risk_banner(score: int) -> None:
    if score >= 60:
        cls, label, desc = "high", "HIGH RISK", "Multiple billing concerns detected"
    elif score >= 30:
        cls, label, desc = "medium", "MEDIUM RISK", "Some issues worth reviewing"
    else:
        cls, label, desc = "low", "LOW RISK", "Bill appears reasonable"
    st.markdown(
        f"""<div class="risk-banner {cls}">
            <div class="risk-left">
                <div class="risk-label">{label}</div>
                <div class="risk-desc">{desc}</div>
            </div>
            <div class="risk-right">
                <div class="risk-score-num">{score}</div>
                <div class="risk-score-lbl">/ 100</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_savings_callout(amount: float) -> None:
    if amount <= 0:
        return
    st.markdown(
        f"""<div class="savings-callout">
            <div class="sc-left">
                <div class="sc-eyebrow">Potential savings if issues corrected</div>
                <div class="sc-amount">{format_currency(amount)}</div>
                <div class="sc-note">Based on flagged billing anomalies</div>
            </div>
            <div class="sc-right">💰</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_itemized_table(rows: List[Tuple[str, str, str]]) -> None:
    rows_html = "".join(
        f'<tr><td class="td-cat">{cat}</td><td class="td-amt">{amt}</td><td>{exp}</td></tr>'
        for cat, amt, exp in rows
    )
    st.markdown(
        f"""<div class="itbl"><table>
            <thead><tr><th>Category</th><th>Amount</th><th>Explanation</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table></div>""",
        unsafe_allow_html=True,
    )


def render_finance_section(lines: List[str], total_billed: float, insurance_paid: float,
                           patient_owes: float, potential_savings: float) -> None:
    if lines:
        rows_html = ""
        for line in lines:
            clean = re.sub(r"^[-*]\s*", "", line)
            lower = clean.lower()
            hot = "potential overcharge" in lower or "savings" in lower
            val_match = re.search(r"\$[\d,]+", clean)
            if val_match:
                label = clean[:val_match.start()].strip().rstrip(":").strip()
                val = clean[val_match.start():].strip()
            else:
                label, val = clean, ""
            rows_html += (
                f'<div class="finance-row">'
                f'<span class="fkey">{label}</span>'
                f'<span class="fval{"  hot" if hot else ""}">{val}</span>'
                f'</div>'
            )
    else:
        rows = [
            ("Total Billed", format_currency(total_billed), False),
            ("Insurance Paid", format_currency(insurance_paid), False),
            ("Patient Owes", format_currency(patient_owes), False),
            ("Potential Savings Opportunity", format_currency(potential_savings), True),
        ]
        rows_html = "".join(
            f'<div class="finance-row">'
            f'<span class="fkey">{lbl}</span>'
            f'<span class="fval{" hot" if hot else ""}">{val}</span>'
            f'</div>'
            for lbl, val, hot in rows
        )
    st.markdown(f'<div class="finance-wrap">{rows_html}</div>', unsafe_allow_html=True)


def render_financial_chart(total_billed: float, insurance_paid: float,
                           patient_owes: float, potential_savings: float) -> None:
    try:
        import pandas as pd
        data = {"Amount ($)": [total_billed, insurance_paid, patient_owes, potential_savings]}
        df = pd.DataFrame(data, index=["Total Billed", "Insurance Paid", "Patient Owes", "Potential Savings"])
        df = df[df["Amount ($)"] > 0]
        if not df.empty:
            st.bar_chart(df, use_container_width=True)
    except ImportError:
        pass


# ─────────────────────────── page sections ───────────────────────────

def render_landing_page() -> None:
    # Override background to dark for landing page only
    st.markdown(
        """<style>
        .stApp { background: #050d1a !important; background-image: none !important; }
        .lp-sec-title { color: rgba(255,255,255,0.88) !important; }
        .lp-sec-label { color: rgba(255,255,255,0.32) !important; }
        </style>""",
        unsafe_allow_html=True,
    )

    # ── Hero ──────────────────────────────────────────────────────────
    st.markdown(
        """<div class="lp-hero">
            <div class="lp-g1"></div>
            <div class="lp-g2"></div>
            <div class="lp-g3"></div>
            <div style="position:relative;z-index:2">
                <div class="lp-brand">
                    🛡️ Bill<span style="background:linear-gradient(135deg,#22d3ee 0%,#a5b4fc 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">Guard AI</span>
                </div>
                <div class="lp-badge">AI-Powered Medical Bill Auditor &nbsp;·&nbsp; PDX Hacks 2026</div>
                <div class="lp-title">
                    Stop Overpaying<br>
                    <span class="lp-accent">for Healthcare.</span>
                </div>
                <div class="lp-subtitle">
                    Medical bills are confusing by design. BillGuard AI decodes every charge,
                    cross-checks your insurance EOB, catches billing errors, and hands you
                    a dispute letter — in under 30 seconds.
                </div>
            </div>
            <svg class="lp-ecg" viewBox="0 0 1200 52" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M0,26 L220,26 L240,26 L258,6 L276,46 L290,12 L304,42 L318,26 L520,26 L540,26 L558,6 L576,46 L590,12 L604,42 L618,26 L820,26 L840,26 L858,6 L876,46 L890,12 L904,42 L918,26 L1200,26"
                      stroke="rgba(6,182,212,0.45)" stroke-width="1.5" fill="none"
                      stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <div class="lp-stats" style="position:relative;z-index:2">
                <div class="lp-stat">
                    <span class="lp-stat-n">~80%</span>
                    <span class="lp-stat-l">of bills contain errors</span>
                </div>
                <div class="lp-stat-div"></div>
                <div class="lp-stat">
                    <span class="lp-stat-n">$1,300</span>
                    <span class="lp-stat-l">avg overcharge</span>
                </div>
                <div class="lp-stat-div"></div>
                <div class="lp-stat">
                    <span class="lp-stat-n">$10B+</span>
                    <span class="lp-stat-l">annual billing errors</span>
                </div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Primary CTA
    _, cta_col, _ = st.columns([1.5, 2, 1.5])
    if cta_col.button("🔍 Analyze My Bill →", type="primary", use_container_width=True):
        st.session_state.page = "app"
        st.rerun()

    st.markdown(
        '<div class="lp-sec-label">✦ &nbsp;What BillGuard Shows You</div>'
        '<div class="lp-sec-title">From messy bill to clear action</div>',
        unsafe_allow_html=True,
    )
    st.image("hero-demo.png", use_container_width=True)

    # ── CARE Framework ─────────────────────────────────────────────────
    st.markdown(
        '<div class="lp-sec-label">✦ &nbsp;The CARE Framework</div>'
        '<div class="lp-sec-title">How BillGuard Works</div>',
        unsafe_allow_html=True,
    )
    render_care_framework()

    # ── Why it matters ─────────────────────────────────────────────────
    st.markdown(
        """<div class="lp-why">
            <div class="lp-sec-label" style="color:rgba(255,255,255,0.35)">✦ &nbsp;Why It Matters</div>
            <div class="lp-sec-title" style="color:white;margin-bottom:0">
                The Problem Is Bigger<br>Than You Think
            </div>
            <div class="lp-why-grid">
                <div class="lp-why-card">
                    <span class="lp-why-icon">🔬</span>
                    <div class="lp-why-num">1 in 3</div>
                    <div class="lp-why-lbl">patients receive an incorrect or inflated medical bill</div>
                </div>
                <div class="lp-why-card">
                    <span class="lp-why-icon">⚖️</span>
                    <div class="lp-why-num">76%</div>
                    <div class="lp-why-lbl">of disputed charges are corrected in the patient's favor</div>
                </div>
                <div class="lp-why-card">
                    <span class="lp-why-icon">⏱️</span>
                    <div class="lp-why-num">&lt; 30s</div>
                    <div class="lp-why-lbl">for BillGuard AI to fully audit and flag your entire bill</div>
                </div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Trust strip ─────────────────────────────────────────────────────
    st.markdown(
        """<div class="lp-trust">
            <span class="lp-trust-item">⚡ Powered by Claude Sonnet 4.6</span>
            <span class="lp-trust-sep">·</span>
            <span class="lp-trust-item">🩺 Patient-First Design</span>
            <span class="lp-trust-sep">·</span>
            <span class="lp-trust-item">🔒 HIPAA-Aware</span>
            <span class="lp-trust-sep">·</span>
            <span class="lp-trust-item">🏆 PDX Hacks 2026</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Bottom CTA block ────────────────────────────────────────────────
    st.markdown(
        """<div class="lp-cta-block">
            <div style="position:relative;z-index:2">
                <div class="lp-cta-eyebrow">✦ &nbsp;Ready to audit your bill?</div>
                <div class="lp-cta-title">Fight Back Against<br>Medical Overcharges.</div>
                <div class="lp-cta-sub">No account needed. Takes less than 30 seconds.</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
    _, cta2_col, _ = st.columns([1.5, 2, 1.5])
    if cta2_col.button("Get Started — It's Free →", use_container_width=True):
        st.session_state.page = "app"
        st.rerun()

    st.markdown(
        """<div class="app-footer">
            PDX Hacks 2026 &nbsp;·&nbsp; Powered by Claude Sonnet 4.6 &nbsp;·&nbsp; Patient-first transparency<br>
            <span style="opacity:0.45">Not a substitute for professional medical billing advice.</span>
        </div>""",
        unsafe_allow_html=True,
    )


def render_care_framework() -> None:
    st.markdown(
        """<div class="lp-care-grid">
            <div class="lp-care-card">
                <div class="lp-care-icon">🔎</div>
                <div class="lp-care-step">C — Clarify</div>
                <div class="lp-care-title">Itemize &amp; translate the bill</div>
                <div class="lp-care-desc">Every CPT code and medical term is decoded into plain English so you know exactly what you were charged for.</div>
            </div>
            <div class="lp-care-card">
                <div class="lp-care-icon">🩺</div>
                <div class="lp-care-step">A — Audit</div>
                <div class="lp-care-title">Detect errors &amp; inconsistencies</div>
                <div class="lp-care-desc">AI cross-checks your bill against your insurance EOB, flags duplicates, inflated fees, and billing anomalies.</div>
            </div>
            <div class="lp-care-card">
                <div class="lp-care-icon">✉️</div>
                <div class="lp-care-step">R — Respond</div>
                <div class="lp-care-title">Generate dispute letter / script</div>
                <div class="lp-care-desc">Get a ready-to-send dispute letter and a phone script you can use with your provider or insurer today.</div>
            </div>
            <div class="lp-care-card">
                <div class="lp-care-icon">💪</div>
                <div class="lp-care-step">E — Empower</div>
                <div class="lp-care-title">Patient confidence + financial clarity</div>
                <div class="lp-care-desc">Walk away knowing your rights, your risk score, and exactly how much you may be able to save.</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.set_page_config(page_title="BillGuard AI", page_icon="🛡️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            """<div style="padding:0.5rem 0 1.25rem">
                <div style="font-size:1.2rem;font-weight:900;color:#0f172a;letter-spacing:-0.5px">
                    🛡️ BillGuard AI
                </div>
                <div style="font-size:0.78rem;color:#94a3b8;margin-top:3px">Medical bill audit tool</div>
            </div>""",
            unsafe_allow_html=True,
        )
        api_key = st.text_input(
            "Anthropic API Key",
            type="password",
            placeholder="sk-ant-...",
            help="Paste the event QR code key here.",
        )
        st.markdown(
            f'<div style="font-size:0.74rem;color:#94a3b8;margin-top:0.2rem">'
            f'Model: <code style="font-size:0.74rem">{MODEL_NAME}</code></div>',
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown(
            '<div style="font-size:0.72rem;font-weight:700;color:#64748b;'
            'text-transform:uppercase;letter-spacing:0.8px;margin-bottom:0.6rem">Compliance Notes</div>',
            unsafe_allow_html=True,
        )
        for ref in COMPLIANCE_REFS:
            st.markdown(
                f'<div style="font-size:0.79rem;color:#475569;padding:0.4rem 0;'
                f'border-bottom:1px solid rgba(15,23,42,0.05);line-height:1.5">{ref}</div>',
                unsafe_allow_html=True,
            )
        st.divider()
        st.markdown(
            """<div style="background:linear-gradient(135deg,#ede9fe,#e0e7ff);
                        border-radius:11px;padding:0.9rem;font-size:0.8rem;color:#4338ca;line-height:1.55">
                💡 <strong>Tip:</strong> Upload both the provider bill <em>and</em> the insurance EOB
                for the most accurate cross-comparison and flag detection.
            </div>""",
            unsafe_allow_html=True,
        )
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
    render_risk_banner(risk_score)
    render_savings_callout(potential_savings)
    render_metrics(total_billed, insurance_paid, patient_owes, potential_savings)
    render_financial_chart(total_billed, insurance_paid, patient_owes, potential_savings)

    if insurance_context.strip():
        st.markdown(
            '<div class="eob-badge">📋 &nbsp;BillGuard compared the provider bill with your '
            'insurance explanation of benefits — mismatches are flagged below.</div>',
            unsafe_allow_html=True,
        )

    # Summary
    section_header("📋", "Summary", "#dcfce7", "#16a34a")
    if sections["SUMMARY"]:
        cleaned_summary = [
            re.sub(r"^[-*]\s*", "", line).strip()
            for line in sections["SUMMARY"]
            if re.sub(r"^[-*]\s*", "", line).strip() not in {"---", "--", "-"}
        ]
        lead = cleaned_summary[0] if cleaned_summary else ""
        points = "".join(
            f'<div class="summary-point"><span class="summary-dot"></span><span>{line}</span></div>'
            for line in cleaned_summary[1:]
        )
        st.markdown(
            f'<div class="summary-card">'
            f'<div class="summary-lead">{lead}</div>'
            f'<div class="summary-points">{points}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.warning("Claude returned no explicit summary. Review the raw response below.")

    # Itemized breakdown
    section_header("🧾", "Itemized Breakdown", "#e0f2fe", "#0284c7")
    itemized_rows = parse_itemized_lines(sections["ITEMIZED"])
    if itemized_rows:
        render_itemized_table(itemized_rows)
    else:
        st.info("No itemized rows were parsed. Review the raw response below.")

    # Financial impact
    section_header("💵", "Financial Impact", "#fef9c3", "#ca8a04")
    render_finance_section(sections["INSURANCE"], total_billed, insurance_paid,
                           patient_owes, potential_savings)

    # Red flags
    section_header("🚩", "Red Flags", "#fee2e2", "#dc2626")
    combined_flags = sections["FLAGS"][:]
    if not combined_flags:
        combined_flags = [f"- {f}" for f in local_flags]
    else:
        combined_flags.extend([f"- {f}" for f in local_flags if f not in " ".join(combined_flags)])

    if combined_flags:
        for index, flag in enumerate(combined_flags, start=1):
            clean = re.sub(r"^[-*]\s*", "", flag)
            if clean.strip() in {"-", "--", ""}:
                continue
            severity = flag_severity(clean)
            st.markdown(
                f'<div class="flag-card err"><span class="fi">🚩</span>'
                f'<div class="flag-copy">'
                f'<div class="flag-topline">'
                f'<span class="flag-pill {severity.split()[0]}">{severity}</span>'
                f'<span class="flag-title">{summarize_flag(clean)}</span>'
                f'</div>'
                f'<div class="flag-subtext">{concise_flag_detail(clean)}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander(f"What to ask for flag {index}"):
                st.markdown(
                    f'<div class="flag-detail">{short_flag_explainer(clean)}</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            '<div class="flag-card ok"><span class="fi">✅</span>'
            '<span class="ft">No major red flags identified — but always review with your provider.</span></div>',
            unsafe_allow_html=True,
        )

    # Action plan
    section_header("🗂️", "Action Plan", "#eff6ff", "#2563eb")
    if sections["ACTION_PLAN"]:
        action_plan_text = "\n".join(re.sub(r"^[-*]\s*", "", line) for line in sections["ACTION_PLAN"])
        with st.expander("View action plan", expanded=True):
            st.text_area(
                "Action plan",
                value=action_plan_text,
                height=260,
                disabled=True,
            )
    elif sections["GUIDANCE"]:
        guidance_html = "".join(
            f"<p>{re.sub(r'^[-*]\\s*', '', line)}</p>" for line in sections["GUIDANCE"]
        )
        st.markdown(f'<div class="guidance-card">{guidance_html}</div>', unsafe_allow_html=True)
    else:
        st.info("No action plan was parsed. Review the raw response below.")

    # Dispute letter
    section_header("✉️", "Case-Specific Dispute Letter", "#f0fdf4", "#16a34a")
    if sections["DISPUTE_LETTER"]:
        dispute_letter_text = "\n".join(
            line if line.strip() else ""
            for line in sections["DISPUTE_LETTER"]
        ).strip()
        with st.expander("View & download dispute letter", expanded=True):
            st.text_area(
                "Dispute letter",
                value=dispute_letter_text,
                height=360,
                disabled=True,
            )
            st.download_button(
                label="⬇ Download Dispute Letter (.txt)",
                data=dispute_letter_text,
                file_name="bill_dispute_letter.txt",
                mime="text/plain",
                use_container_width=True,
            )
    else:
        st.info("No dispute letter was parsed. Review the raw response below.")

    if sections["PHONE_SCRIPT"]:
        with st.expander("Call script"):
            phone_script = "\n".join(re.sub(r"^[-*]\s*", "", line) for line in sections["PHONE_SCRIPT"])
            st.text_area(
                "Phone script",
                value=phone_script,
                height=180,
                disabled=True,
            )

    with st.expander("Show Claude raw response"):
        st.code(raw_output, language="text")
def render_followup_chat(api_key: str, bill_text: str, insurance_text: str, analysis_text: str) -> None:
    st.markdown('<div class="cdiv"></div>', unsafe_allow_html=True)
    section_header("💬", "Ask Follow-Up Questions", "#e0e7ff", "#4f46e5")
    st.markdown(
        '<div style="font-size:0.85rem;color:#64748b;margin-bottom:1rem">'
        'Ask Claude anything about your bill — codes, next steps, what to say to your insurer.'
        '</div>',
        unsafe_allow_html=True,
    )

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

    render_savings_callout(estimated_savings)
    render_metrics(
        fallback_totals["total_billed"],
        fallback_totals["insurance_paid"],
        fallback_totals["patient_responsibility"],
        estimated_savings,
    )

    st.warning(
        "API connection issue. This is common on event Wi-Fi. "
        "Try re-entering your key or ask an organizer with an orange badge."
    )

    section_header("🚩", "Local Risk Flags", "#fee2e2", "#dc2626")
    if fallback_flags:
        for flag in fallback_flags:
            st.markdown(
                f'<div class="flag-card err"><span class="fi">🚩</span>'
                f'<span class="ft">{flag}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            '<div class="flag-card ok"><span class="fi">✅</span>'
            '<span class="ft">No obvious risks detected locally — Claude analysis recommended.</span></div>',
            unsafe_allow_html=True,
        )

    section_header("✉️", "Immediate Patient Script", "#f0fdf4", "#16a34a")
    st.markdown(
        '<div class="guidance-card">'
        "<p>Hello, I reviewed this bill and I need an itemized explanation of each charge, "
        "especially any repeated lab fees and the facility fee.</p>"
        "<p>Please confirm whether any charges were duplicated, explain how my patient "
        "responsibility was calculated, and send a corrected statement if errors are found.</p>"
        "</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────── main ────────────────────────────────────

def main() -> None:
    render_header()

    # Bootstrap all session state keys once
    for key, default in [
        ("page", "landing"),
        ("bill_text", ""),
        ("insurance_text", ""),
        ("last_analysis", ""),
        ("last_bill", ""),
        ("last_insurance", ""),
        ("analysis_ready", False),
        ("chat_history", []),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Landing page ──────────────────────────────────────────────────
    if st.session_state.page == "landing":
        render_landing_page()
        return

    # ── App page ──────────────────────────────────────────────────────
    api_key = render_sidebar()

    if st.button("← Back to Home"):
        st.session_state.page = "landing"
        st.rerun()

    section_header("📄", "Upload or Paste Your Bill", "#e0f2fe", "#0284c7")

    upload_col1, upload_col2 = st.columns(2)
    uploaded_bill_file = upload_col1.file_uploader(
        "Upload provider bill",
        type=["pdf", "json", "txt"],
        help="Upload the provider bill as a PDF, text, or JSON file.",
        key="provider_upload",
    )
    uploaded_insurance_file = upload_col2.file_uploader(
        "Upload insurance EOB",
        type=["pdf", "json", "txt"],
        help="Upload an explanation of benefits or insurer summary to compare against the bill.",
        key="insurance_upload",
    )
    for uploaded_file, session_key, label in [
        (uploaded_bill_file, "bill_text", "Provider bill"),
        (uploaded_insurance_file, "insurance_text", "Insurance EOB"),
    ]:
        if uploaded_file is not None:
            try:
                uploaded_text, upload_message = extract_text_from_upload(uploaded_file)
                st.session_state[session_key] = uploaded_text
                st.session_state.analysis_ready = False
                st.session_state.chat_history = []
                st.success(f"{label}: {upload_message}")
            except Exception as exc:
                st.error(str(exc))

    st.markdown(
        '<div class="micro-note">Informational only. Not medical or legal advice. '
        "Always verify with your provider or insurer.</div>",
        unsafe_allow_html=True,
    )

    bill_text = st.session_state.get("bill_text", "")
    insurance_text = st.session_state.get("insurance_text", "")

    analyze = st.button("🔍 Analyze My Bill", type="primary", use_container_width=True)

    if analyze:
        if not bill_text.strip():
            st.warning("Paste bill text or upload a file before running the analysis.")
            return
        if not api_key.strip():
            st.warning("Enter your Anthropic API key in the sidebar to run Claude.")
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
        """<div class="app-footer">
            PDX Hacks 2026 &nbsp;·&nbsp; Built in 3 hours &nbsp;·&nbsp;
            Powered by Claude Sonnet 4.6 &nbsp;·&nbsp; Patient-first transparency<br>
            <span style="opacity:0.45">Not a substitute for professional medical billing advice.</span>
        </div>""",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
