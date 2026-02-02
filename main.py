import requests
from bs4 import BeautifulSoup
import pandas as pd

def fetch_sfc_matches():
    # 500网胜负彩首页（默认显示当前最新期号）
    url = "https://trade.500.com/sfc/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers)
        # 关键：500网使用GBK编码，必须手动指定否则中文会乱码
        response.encoding = 'gbk' 
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 定位对阵表格 (500网通常使用 vs_table 类名)
        # 注意：实际抓取时需要根据网页当前的 id 或 class 微调
        match_rows = soup.select('tr[data-vs]') # 500网特有的行属性
        
        data_list = []
        for row in match_rows[:14]: # 只取前14场
            tds = row.find_all('td')
            bjpl_str = row.get('data-bjpl', "")
            odds = bjpl_str.split(',') if bjpl_str else ["", "", ""]
            # 提取基础信息：场次、联赛、时间、主队、客队
            item = {
                "场次": tds[0].text.strip(),
                "联赛": tds[1].text.strip(),
                "开赛时间": tds[2].text.strip(),
                "主队": row.select_one('.team-l a').text.strip(),
                "客队": row.select_one('.team-r a').text.strip(),
                # 新增赔率字段
                "胜赔": odds[0],
                "平赔": odds[1],
                "负赔": odds[2],
                "盘口": row.get('data-asian', "").split(',')[1] if row.get('data-asian') else ""
            }
            data_list.append(item)
            
        # 使用 Pandas 格式化输出
        df = pd.DataFrame(data_list)
        return df

    except Exception as e:
        return f"抓取失败: {e}"

if __name__ == "__main__":
    matches_df = fetch_sfc_matches()
    
    # 检查返回的是不是 Pandas 表格
    if isinstance(matches_df, pd.DataFrame):
        print("--- 传统足彩14场最新对阵表 ---")
        print(matches_df.to_string(index=False))
    else:
        # 如果是字符串，说明抓取失败了，直接打印错误原因
        print("出错了！具体原因是：")
        print(matches_df)