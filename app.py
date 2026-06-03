import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import gspread
from google.oauth2.service_account import Credentials
import time

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Housing Map", page_icon="🏠", layout="wide")

# ── Google Sheets connection ───────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_worksheet():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(st.secrets["sheet_id"])
    return sheet.sheet1

def load_data(ws):
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    # Ensure correct types; blank lat/lon come back as empty string
    for col in ["lat", "lon"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def save_latlon(ws, row_index, lat, lon):
    # row_index is 0-based pandas index; sheet rows are 1-based + 1 header row
    sheet_row = row_index + 2
    ws.update_cell(sheet_row, 2, lat)   # column B = lat
    ws.update_cell(sheet_row, 3, lon)   # column C = lon

def save_comment(ws, row_index, comment):
    sheet_row = row_index + 2
    ws.update_cell(sheet_row, 5, comment)  # column E = comment

# ── Geocoding ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_geocoder():
    geolocator = Nominatim(user_agent="housing_map_app")
    return RateLimiter(geolocator.geocode, min_delay_seconds=1)

def geocode_missing(df, ws):
    geocode = get_geocoder()
    needs_geocoding = df[df["lat"].isna() | df["lon"].isna()]
    if needs_geocoding.empty:
        return df

    progress = st.progress(0, text="Geocoding new addresses…")
    for i, (idx, row) in enumerate(needs_geocoding.iterrows()):
        location = geocode(row["address"])
        if location:
            df.at[idx, "lat"] = location.latitude
            df.at[idx, "lon"] = location.longitude
            save_latlon(ws, idx, location.latitude, location.longitude)
        progress.progress(
            (i + 1) / len(needs_geocoding),
            text=f"Geocoding {i+1}/{len(needs_geocoding)}: {row['address']}"
        )
    progress.empty()
    return df

# ── Build map ─────────────────────────────────────────────────────────────────
def build_map(df, selected_idx=None):
    valid = df.dropna(subset=["lat", "lon"])
    if valid.empty:
        center = [34.0, -118.0]  # fallback: Los Angeles area
        zoom = 9
    else:
        center = [valid["lat"].mean(), valid["lon"].mean()]
        zoom = 11

    m = folium.Map(location=center, zoom_start=zoom, tiles="CartoDB positron")

    for idx, row in valid.iterrows():
        is_selected = (idx == selected_idx)
        color = "red" if is_selected else "blue"
        url_html = (
            f'<a href="{row["url"]}" target="_blank">{row["url"]}</a>'
            if row.get("url") else "—"
        )
        comment_html = row["comment"] if row.get("comment") else "—"
        popup_html = f"""
        <div style="min-width:220px; font-family:sans-serif; font-size:13px;">
            <b>{row['address']}</b><br><br>
            <b>URL:</b> {url_html}<br><br>
            <b>Comment:</b> {comment_html}
        </div>
        """
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=row["address"],
            icon=folium.Icon(color=color, icon="home", prefix="fa"),
        ).add_to(m)

    return m

# ── Main app ───────────────────────────────────────────────────────────────────
st.title("🏠 Housing Map")

ws = get_worksheet()

if "df" not in st.session_state:
    st.session_state.df = load_data(ws)
    st.session_state.df = geocode_missing(st.session_state.df, ws)

if "selected_idx" not in st.session_state:
    st.session_state.selected_idx = None

df = st.session_state.df

# ── Map ───────────────────────────────────────────────────────────────────────
m = build_map(df, selected_idx=st.session_state.selected_idx)
map_data = st_folium(m, width="100%", height=520, returned_objects=["last_object_clicked_tooltip"])

# Detect marker click by tooltip (address)
clicked_address = (map_data or {}).get("last_object_clicked_tooltip")
if clicked_address:
    match = df[df["address"] == clicked_address]
    if not match.empty:
        st.session_state.selected_idx = match.index[0]

# ── Edit panel ────────────────────────────────────────────────────────────────
st.divider()

if st.session_state.selected_idx is not None:
    idx = st.session_state.selected_idx
    row = df.loc[idx]

    st.subheader(f"📍 {row['address']}")

    if row.get("url"):
        st.markdown(f"**URL:** [{row['url']}]({row['url']})")
    else:
        st.markdown("**URL:** —")

    new_comment = st.text_area(
        "Comment",
        value=row["comment"] if row.get("comment") else "",
        height=120,
        key=f"comment_{idx}",
    )

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("💾 Save", type="primary"):
            st.session_state.df.at[idx, "comment"] = new_comment
            save_comment(ws, idx, new_comment)
            st.success("Saved!")
            time.sleep(1)
            st.rerun()
    with col2:
        if st.button("✖ Deselect"):
            st.session_state.selected_idx = None
            st.rerun()
else:
    st.info("Click a map marker to select a property and edit its comment.")

# ── Refresh button ────────────────────────────────────────────────────────────
st.divider()
if st.button("🔄 Reload data from sheet"):
    st.cache_resource.clear()
    st.session_state.df = load_data(ws)
    st.session_state.df = geocode_missing(st.session_state.df, ws)
    st.rerun()
