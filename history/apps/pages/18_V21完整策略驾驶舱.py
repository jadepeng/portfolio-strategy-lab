"""V21 offline full dashboard inside the existing Streamlit strategy lab."""
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

ROOT=Path(__file__).resolve().parents[3]
FILE=ROOT/'web'/'V21_策略研究驾驶舱.html'
st.set_page_config(page_title='V21 中美双市场策略驾驶舱',layout='wide')
st.title('V21 · 中美双市场策略驾驶舱')
st.caption('离线历史快照截至2026-11-20。A股V18、美股V18为冻结基准，V20为研究对照。')
st.warning('本页不提供实时行情或交易下单；历史收益不能代表未来。建议下载独立版网页，以获得完整响应式布局。')
if not FILE.exists():
    st.error('尚未找到V21网页，先执行 python scripts/build_v21_dashboard.py')
else:
    st.download_button('下载可独立打开的完整HTML网页',FILE.read_bytes(),
                       file_name='V21_中美双市场策略驾驶舱.html',mime='text/html')
    components.html(FILE.read_text(encoding='utf-8'),height=1430,scrolling=True)
