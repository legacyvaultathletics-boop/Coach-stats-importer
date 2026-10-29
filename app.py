import streamlit as st
import pandas as pd
import io, re, time
from curl_cffi import requests
from google import genai

# Page Setup
st.set_page_config(page_title="CoachStats AI", page_icon="🏀", layout="wide")
st.title("🏀 Turnkey High School Stat Importer")
st.write("Paste a MaxPreps stats link below to automatically consolidate rosters and career stats for Google AI Studio.")

# User Inputs
team_url = st.text_input("MaxPreps Team Stats URL:", placeholder="https://www.maxpreps.com/nc/greensboro/page-pirates/basketball/stats/")

col1, col2 = st.columns(2)
with col1:
    start_year = st.selectbox("Start Season", ["20-21", "21-22", "22-23", "23-24", "24-25", "25-26"], index=1)
with col2:
    end_year = st.selectbox("End Season", ["24-25", "25-26", "26-27"], index=1)

if st.button("🚀 Fetch All Seasons", type="primary"):
    if not team_url:
        st.warning("Please enter a valid MaxPreps URL.")
    else:
        with st.spinner("Scanning historical seasons in the background..."):
            # Clean URL
            clean_base = re.sub(r'/\d{2}-\d{2}/', '/', team_url.rstrip('/'))
            template_url = (clean_base[:-6] if clean_base.endswith('/stats') else clean_base) + "/{season}/stats/"
            school_name = template_url.split('/')[5].replace('-', ' ').title()

            # Generate season list
            all_years = ["15-16", "16-17", "17-18", "18-19", "19-20", "20-21", "21-22", "22-23", "23-24", "24-25", "25-26", "26-27"]
            try:
                s_idx = all_years.index(start_year)
                e_idx = all_years.index(end_year)
                selected_seasons = all_years[s_idx:e_idx+1]
            except ValueError:
                selected_seasons = ["23-24", "24-25", "25-26"]

            frames = []
            for s in selected_seasons:
                target = template_url.format(season=s)
                res = requests.get(target, impersonate="chrome120")
                if res.status_code == 200 and "<table" in res.text:
                    try:
                        tables = pd.read_html(io.StringIO(res.text))
                        if tables:
                            df = tables[0]
                            df.insert(0, "Season", s)
                            df.insert(0, "School", school_name)
                            frames.append(df)
                    except Exception:
                        pass
                time.sleep(0.3)

            if frames:
                master_df = pd.concat(frames, ignore_index=True)
                st.success(f"✓ Successfully aggregated {len(frames)} active seasons for {school_name}!")
                
                # Show Preview
                st.dataframe(master_df.head(10))

                # Download Button for Coaches
                csv_data = master_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Master CSV for AI Studio",
                    data=csv_data,
                    file_name=f"{school_name.replace(' ', '_')}_stats.csv",
                    mime="text/csv"
                )
            else:
                st.error("No player stat tables were found for the selected seasons. (Note: Many high school coaches only submit scores, not individual player stats).")

