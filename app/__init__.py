import streamlit as st

from core.application.mobility_service import MobilityService


@st.cache_resource(show_spinner="Loading transport data into DuckDB…")
def get_service() -> MobilityService:
    return MobilityService.create()


def require_service() -> MobilityService:
    try:
        return get_service()
    except Exception as exc:
        st.error(f"Mobility Pulse could not load the required files from data/: {exc}")
        st.stop()
