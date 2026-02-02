import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import sqlite3
from datetime import datetime
from betting_processor import BettingProcessor

# --- 配置区 ---
DB_NAME = "football_lottery.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- 数据库逻辑 ---
def init_db():
    """初始化数据库表结构"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sfc_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT,
            match_no TEXT,
            league TEXT,
            match_time TEXT,
            home_team TEXT,
            away_team TEXT,
            odds_win TEXT,
            odds_draw TEXT,
            odds_loss TEXT,
            handicap TEXT,
            scrape_time DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def get_handicap_prob(handicap_text):
    # 1. 盘口转数字映射
    mapping = {
        "平手": 0.0, "平/半": 0.25, "半球": 0.5, "半/一": 0.75,
        "一球": 1.0, "一/球半": 1.25, "球半": 1.5, "球半/两": 1.75
    }
    h_value = mapping.get(handicap_text, 0.0)
    
    # 2. 盘口转基准概率 (简化线性模型：0.5让球对应50%胜率)
    # 基础公式：胜率 = 0.38 (平手基准) + 让球数 * 0.25
    # 这是一个经验公式，可以根据后续Agent复盘不断修正
    implied_prob = 0.38 + (h_value * 0.24)
    return min(implied_prob, 0.95) # 最高不超过95%

def calculate_synthetic_prob(win_odds, draw_odds, loss_odds, handicap_text):
    # 计算欧指去抽水胜率 (Pe)
    p_w = 1 / float(win_odds)
    p_d = 1 / float(draw_odds)
    p_l = 1 / float(loss_odds)
    pe_win = p_w / (p_w + p_d + p_l)
    
    # 计算盘口隐含胜率 (Ph)
    ph_win = get_handicap_prob(handicap_text)
    
    # 最终合成：60%欧指权重 + 40%盘口权重
    final_win_prob = (pe_win * 0.6) + (ph_win * 0.4)
    return round(final_win_prob * 100, 2)

def save_to_sqlite(df, period):
    """将数据保存至SQLite，避免重复写入同一期"""
    conn = sqlite3.connect(DB_NAME)
    # 先删除该期旧数据（防止重复抓取导致数据堆积）
    conn.execute("DELETE FROM sfc_matches WHERE period = ?", (period,))
    df.to_sql('sfc_matches', conn, if_exists='append', index=False)
    conn.close()

# --- 抓取逻辑 ---
def fetch_data():
    """从500网抓取最新对阵和赔率"""
    url = "https://trade.500.com/sfc/"
    res = requests.get(url, headers=HEADERS)
    res.encoding = 'gbk'
    soup = BeautifulSoup(res.text, 'lxml')
    
    # --- 1. 抓取截止时间 (不存库) ---
    deadline = "未知"
    endtime_element = soup.select_one('.zcfilter-endtime')
    if endtime_element:
        # 提取 "01-29 22:00" 部分
        deadline = endtime_element.text.replace("官方售彩截止时间：", "").strip()

    # 自动获取当前期号
    # 定位到 class 为 chked 的 li 标签
    period_element = soup.select_one('.qih-list li.chked')
    if period_element:
        # 优先获取 data-expect 属性，这通常是纯数字期号（如 26020）
        period = period_element.get('data-expect', "").strip()
        
        # 如果没取到属性，再降级尝试解析文字
        if not period:
            period = period_element.text.replace("当前第", "").replace("期", "").strip()
    else:
        period = "未知期号"
    
    match_rows = soup.select('tr[data-vs]')
    data_list = []
    
    for row in match_rows[:14]:
        tds = row.find_all('td')
        bjpl = row.get('data-bjpl', "").split(',')
        asian = row.get('data-asian', "").split(',')
        
        item = {
            "period": period,
            "match_no": tds[0].text.strip(),
            "league": tds[1].text.strip(),
            "match_time": tds[2].text.strip(),
            "home_team": row.select_one('.team-l a').text.strip() if row.select_one('.team-l a') else "未知",
            "away_team": row.select_one('.team-r a').text.strip() if row.select_one('.team-r a') else "未知",
            "odds_win": bjpl[0] if len(bjpl)>0 else "",
            "odds_draw": bjpl[1] if len(bjpl)>1 else "",
            "odds_loss": bjpl[2] if len(bjpl)>2 else "",
            "handicap": asian[1] if len(asian)>1 else "",
            "scrape_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data_list.append(item)
    
    return pd.DataFrame(data_list), period,deadline

def get_analyzed_df(period):
    # 步骤 1：从 SQLite 数据库读取原始数据
    conn = sqlite3.connect("football_lottery.db")
    query = "SELECT * FROM sfc_matches WHERE period = ?"
    raw_df = pd.read_sql(query, conn, params=(period,))
    conn.close()

    if raw_df.empty:
        return None

    # 步骤 2：实例化你的处理器
    proc = BettingProcessor()

    # 步骤 3：加工数据（这一步会生成 胜%、平%、负% 等列）
    # 这就是 select_9_final_logic 所需要的入参
    analyzed_df = proc.process_dataframe(raw_df)
    
    return analyzed_df

def display_recommendation(final_9, total_p):
    st.markdown(f"### 🏆 智能优化方案 (全中概率: **{total_p}%**)")

    # 定义高亮函数
    def highlight_picks(row):
        # 初始化样式：默认无色
        styles = [''] * len(row)
        # 获取各列索引
        cols = list(row.index)
        win_idx, draw_idx, loss_idx = cols.index('胜%'), cols.index('平%'), cols.index('负%')
        
        pick = str(row['建议']) # 如 "3" 或 "3/1"
        
        # 绿色高亮的 CSS
        highlight_css = 'background-color: #27ae60; color: white; font-weight: bold;'
        
        if "3" in pick: styles[win_idx] = highlight_css
        if "1" in pick: styles[draw_idx] = highlight_css
        if "0" in pick: styles[loss_idx] = highlight_css
        
        return styles

    # 选择需要显示的列
    display_df = final_9[['match_no', 'home_team', 'away_team', '胜%', '平%', '负%', '建议', '投法']]
    
    # 应用样式
    st.dataframe(
        display_df.style.apply(highlight_picks, axis=1),
        use_container_width=True,
        hide_index=True
    )

   


# --- Streamlit 界面 ---
def main():
    st.set_page_config(page_title="足彩数据看板", layout="wide")
    st.title("⚽ 传统足彩 14 场实时数据中心")
    
    init_db()
    
    col1, col2 = st.columns([1, 3])
    deadline = "未知"
    
    with col1:
        st.subheader("控制面板")
        if st.button("🚀 抓取最新对阵"):
            with st.spinner('正在同步500网数据...'):
                try:
                    df, period,deadline = fetch_data()
                    save_to_sqlite(df, period)
                    st.success(f"期号 {period} 已成功入库！")
                except Exception as e:
                    st.error(f"抓取失败: {e}")
        
        st.write("---")
        st.info("提示：点击按钮后，数据将自动保存至本地 football_lottery.db 文件。")

        st.subheader("危险操作")
        if st.button("🗑️ 清理所有未知期号"):
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sfc_matches WHERE period = '未知期号'")
            count = cursor.rowcount
            conn.commit()
            conn.close()
            st.warning(f"已清理 {count} 条脏数据！")
            st.rerun() # 刷新网页查看效果
       
           
           

    with col2:
        st.subheader("当前数据展现")
        st.metric(label="⏳ 本期购买截止时间", value=deadline)
        conn = sqlite3.connect(DB_NAME)
        # 从数据库读取所有抓取过的信息
        all_data = pd.read_sql("SELECT * FROM sfc_matches ORDER BY scrape_time DESC", conn)
        
        
        if not all_data.empty:
            
            # 增加一个筛选器，查看不同期号
            periods = all_data['period'].unique()
            selected_period = st.selectbox("选择要查看的期号", periods)
            
            display_df = all_data[all_data['period'] == selected_period]
            st.table(display_df.drop(columns=['id', 'scrape_time'])) # 隐藏内部ID显示
        else:
            st.warning("数据库目前为空，请点击左侧按钮进行第一次抓取。")

        st.subheader("数据分析看板")
        # 1. 从库里拿原始数据
        raw_df = pd.read_sql("SELECT * FROM sfc_matches WHERE period = ?", conn, params=(selected_period,))
        
        # 2. 调用独立的处理器进行计算
        proc = BettingProcessor()
        analyzed_df = proc.process_dataframe(raw_df)

        # 定义高亮逻辑：概率最大的那一项变绿
        def highlight_max(s):
            is_max = s == s.max()
            return ['background-color: #1b5e20; color: white' if v else '' for v in is_max]

        st.dataframe(
            analyzed_df.style.apply(highlight_max, axis=1, subset=['胜%', '平%', '负%']),
            use_container_width=True
        )

        if st.button("生成智能推荐"):
            conn = sqlite3.connect(DB_NAME)
            # 从数据库读取所有抓取过的信息
            all_data = pd.read_sql("SELECT * FROM sfc_matches ORDER BY scrape_time DESC", conn)
            if not all_data.empty:
                # 增加一个筛选器，查看不同期号
                periods = all_data['period'].unique()
                # 变量在这里定义的
                selected_period = st.selectbox("选择期号", periods) 
                df_for_logic = get_analyzed_df(selected_period)

                proc = BettingProcessor()
                
                if df_for_logic is not None:
                    # 调用你的筛选逻辑函数
                    final_9, total_p = proc.select_9_final_logic(df_for_logic)
                    
                    # B. 调用显示函数（渲染金色卡片和高亮表格）
                    display_recommendation(final_9, total_p)
                else:
                    st.error("该期号下暂无数据，请先执行抓取。")
    
   

    

if __name__ == "__main__":
    main()