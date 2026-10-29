import streamlit as st
import pandas as pd
import io, re, time
from urllib.parse import urlparse
from curl_cffi import requests

# Page Configuration
st.set_page_config(page_title="CoachStats AI", page_icon="🏀", layout="wide")
st.title("🏀 Turnkey High School Stat Importer")
st.write("Extract historical roster and performance tables from MaxPreps (2005–06 to Present) for Google AI Studio.")

# Dynamically generate all 22 seasons: ['05-06', '06-07', ..., '26-27']
ALL_SEASONS = [f"{str(y)[-2:]}-{str(y+1)[-2:]}" for y in range(2005, 2027)]

def clean_maxpreps_url(url):
    """Strips #Player, query tags, and season numbers to build a clean multi-year template."""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip('/')
    parts = [p for p in path.split('/') if p]
    
    # Remove existing season indicators (e.g., 25-26) and trailing 'stats'
    filtered = [p for p in parts if not re.match(r'^\d{2}-\d{2}$', p) and p != 'stats']
    base_path = '/'.join(filtered)
    template = f"https://www.maxpreps.com/{base_path}/{{season}}/stats/"
    
    # Extract clean school name
    school = filtered[2].replace('-', ' ').title() if len(filtered) > 2 else "Team"
    return template, school

# Input Fields
team_url = st.text_input(
    "MaxPreps Team Stats URL:",
    value="https://www.maxpreps.com/nc/high-point/southwest-guilford-cowboys/basketball/girls/stats/"
)

# Range Options
fetch_all = st.checkbox("⚡ Fetch entire 20+ year archive (05-06 through 26-27)", value=True)

if not fetch_all:
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.selectbox("Start Season", ALL_SEASONS, index=0) # defaults to 05-06
    with col2:
        end_year = st.selectbox("End Season", ALL_SEASONS, index=len(ALL_SEASONS)-1) # defaults to 26-27
else:
    start_year = "05-06"
    end_year = "26-27"

if st.button("🚀 Fetch Seasons", type="primary"):
    if not team_url:
        st.warning("Please enter a valid MaxPreps URL.")
    else:
        template_url, school_name = clean_maxpreps_url(team_url)
        st.info(f"Targeting: **{school_name}** | Base URL: `{template_url.format(season='[SEASON]')}`")

        s_idx = ALL_SEASONS.index(start_year)
        e_idx = ALL_SEASONS.index(end_year)
        selected_seasons = ALL_SEASONS[s_idx:e_idx+1]

        frames = []
        progress_bar = st.progress(0)
        status_box = st.empty()

        for i, s in enumerate(selected_seasons):
            target = template_url.format(season=s)
            status_box.write(f"⏳ Scanning season **{s}**...")

            try:
                res = requests.get(target, impersonate="chrome120")
                if res.status_code == 200:
                    tables = pd.read_html(io.StringIO(res.text))
                    if tables:
                        df = tables[0]
                        df.insert(0, "Season", s)
                        df.insert(0, "School", school_name)
                        frames.append(df)
                        st.write(f"✓ **{s}**: Ingested ({len(df)} players)")
                    else:
                        st.write(f"– **{s}**: Page loaded, but no player stats table entered")
                elif res.status_code == 404:
                    st.write(f"– **{s}**: No season page exists (404)")
                else:
                    st.write(f"– **{s}**: Server status {res.status_code}")
            except Exception:
                st.write(f"– **{s}**: Skipped (no table)")

            progress_bar.progress((i + 1) / len(selected_seasons))
            time.sleep(0.3)

        status_box.empty()

        if frames:
            master_df = pd.concat(frames, ignore_index=True)
            st.success(f"🎉 Successfully captured {len(frames)} active seasons for {school_name}!")
            
            st.dataframe(master_df)

            csv_data = master_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Master 20-Year CSV for Google AI Studio",
                data=csv_data,
                file_name=f"{school_name.replace(' ', '_')}_2005_to_Present.csv",
                mime="text/csv"
            )
        else:
            st.error("No player stats found across the selected seasons.")

