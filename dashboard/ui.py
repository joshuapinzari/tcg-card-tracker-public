"""Shared Streamlit presentation helpers.

Keep model/data code separate from presentation so the dashboard can stay dense
when needed without making every page feel visually heavy by default.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def apply_dashboard_style() -> None:
    """Apply a restrained, compact dashboard theme without changing app logic."""
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.45rem;
            padding-bottom: 2rem;
            max-width: 1550px;
        }
        h1 { font-size: 1.70rem !important; margin-bottom: .30rem !important; }
        h2 { font-size: 1.38rem !important; margin-top: .55rem !important; }
        h3 { font-size: 1.15rem !important; margin-top: .55rem !important; }
        [data-testid="stMetric"] {
            padding: .55rem .7rem;
            border: 1px solid rgba(128,128,128,.18);
            border-radius: .55rem;
        }
        [data-testid="stMetricLabel"] { font-size: .82rem; }
        [data-testid="stMetricValue"] { font-size: 1.25rem; }
        [data-testid="stExpander"] { border-radius: .55rem; }
        .stCaptionContainer { line-height: 1.35; }
        div[data-testid="stVerticalBlock"] > div { gap: .55rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def table(frame, columns=None, *, key=None):
    """Render a table only when it contains rows.

    Empty public datasets should look intentional rather than like broken tables.
    Callers provide the explanatory empty-state message separately.
    """
    if frame is None or frame.empty:
        return False
    if columns is not None:
        frame = frame.reindex(columns=columns)
    st.dataframe(frame, hide_index=True, use_container_width=True, key=key)
    return True


def chart(frame, *, x='date', y='price', color=None, title='', y_label='USD', key=None):
    """Render a real chart only when observations exist.

    Missing data stays missing; the public app does not manufacture placeholder
    axes or points just to fill the page.
    """
    import plotly.express as px
    if frame is None or frame.empty or x not in frame or y not in frame:
        return False
    figure = px.line(frame, x=x, y=y, color=color, title=title,
                     labels={x: 'Date', y: y_label})
    figure.update_layout(height=350, margin=dict(l=10, r=10, t=40, b=20))
    st.plotly_chart(figure, use_container_width=True, key=key)
    return True


def model_guide():
    """Explain the two research models without requiring project history."""
    st.info(
        '**How to read the models:** **Frozen V3** is the locked baseline model kept '
        'unchanged so its performance can be evaluated fairly. **Hybrid V4** is a '
        'regime-aware challenger that changes weights by supported market era. The '
        'app uses V4 only where its era mapping applies; otherwise it falls back to V3. '
        'Both scores are relative research rankings, not probabilities of profit.'
    )


def no_data(message='No data is available for these selections.'):
    st.info(message)
