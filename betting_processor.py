import pandas as pd

class BettingProcessor:
    def __init__(self):
        # 数字化映射
        self.handicap_map = {
            "平手": 0.0, "平/半": 0.25, "半球": 0.5, "半/一": 0.75,
            "一球": 1.0, "一/球半": 1.25, "球半": 1.5, "球半/两": 1.75, "两球": 2.0
        }

    def _parse_h(self, text):
        if not text: return 0.0
        key = text.split(',')[1] if ',' in text else text
        return self.handicap_map.get(key, 0.0)

    def calculate_distribution(self, row):
        """
        计算一场比赛的 胜/平/负 完整概率分布
        """
        try:
            # 1. 欧指原始概率 (去抽水)
            o_w, o_d, o_l = float(row['odds_win']), float(row['odds_draw']), float(row['odds_loss'])
            raw_w, raw_d, raw_l = 1/o_w, 1/o_d, 1/o_l
            sum_raw = raw_w + raw_d + raw_l
            
            p_e_w = raw_w / sum_raw
            p_e_d = raw_d / sum_raw
            p_e_l = raw_l / sum_raw

            # 2. 亚盘修正主胜率
            h_val = self._parse_h(row['handicap'])
            p_h_w = 0.38 + (h_val * 0.25)
            
            # 3. 合成最终主胜率 (60/40 加权)
            final_w = (p_e_w * 0.6) + (p_h_w * 0.4)
            final_w = min(max(final_w, 0.05), 0.95) # 边界保护

            # 4. 分配剩余概率给 平 和 负
            remaining = 1.0 - final_w
            ratio_d_l = p_e_d + p_e_l
            
            final_d = remaining * (p_e_d / ratio_d_l)
            final_l = remaining * (p_e_l / ratio_d_l)

            return {
                "胜%": round(final_w * 100, 1),
                "平%": round(final_d * 100, 1),
                "负%": round(final_l * 100, 1)
            }
        except:
            return {"胜%": 0.0, "平%": 0.0, "负%": 0.0}

    def process_dataframe(self, df):
        """
        批量处理 DataFrame，增加三个概率列
        """
        res_list = df.apply(self.calculate_distribution, axis=1).tolist()
        res_df = pd.DataFrame(res_list)
        return pd.concat([df.reset_index(drop=True), res_df], axis=1)
   

    def select_9_greedy_log_optimization(self, df, max_doubles=3):
        """
        贪心对数优化：在14场中寻找理论总胜率最高的9场方案
        max_doubles: 允许的最大双选数量
        """
        df = df.copy()
        # 1. 预计算每场比赛的单选对数和双选增益
        # 使用 epsilon 防止 log(0)
        eps = 1e-9
        df['S1'] = df[['胜%', '平%', '负%']].max(axis=1) / 100.0
        df['S2'] = df[['胜%', '平%', '负%']].apply(lambda x: sum(sorted(x, reverse=True)[:2]), axis=1) / 100.0
        
        # 对数转换：将乘法变加法
        df['log_s1'] = np.log(df['S1'] + eps)
        df['log_s2'] = np.log(df['S2'] + eps)
        # 增益：如果这场从单选变双选，对数概率增加了多少
        df['gain'] = df['log_s2'] - df['log_s1']

        best_score = -np.inf
        best_indices = None
        best_double_indices = None

        # 2. 穷举 14 选 9 的所有组合 (2002 种)
        all_indices = df.index.tolist()
        for combo in combinations(all_indices, 9):
            current_df = df.loc[list(combo)]
            
            # 在这 9 场中，找出增益最大的 3 场进行双选
            # 按 gain 降序排列，取前 max_doubles 个
            gains = current_df['gain'].sort_values(ascending=False)
            double_picks = gains.head(max_doubles).index.tolist()
            single_picks = [i for i in combo if i not in double_picks]
            
            # 计算当前组合的总对数概率
            # 总得分 = (双选场的 log_s2 之和) + (单选场的 log_s1 之和)
            total_log_p = df.loc[double_picks, 'log_s2'].sum() + df.loc[single_picks, 'log_s1'].sum()
            
            if total_log_p > best_score:
                best_score = total_log_p
                best_indices = combo
                best_double_indices = double_picks

        # 3. 构造最终结果表
        final_9 = df.loc[list(best_indices)].copy()
        final_9['投法'] = '单选'
        final_9.loc[best_double_indices, '投法'] = '双选'
        
        # 映射具体选号 (3/1/0)
        def get_tags(row):
            probs = {"3": row['胜%'], "1": row['平%'], "0": row['负%']}
            sorted_tags = sorted(probs, key=probs.get, reverse=True)
            return "/".join(sorted_tags[:2]) if row['投法'] == '双选' else sorted_tags[0]

        final_9['建议'] = final_9.apply(get_tags, axis=1)
        
        # 计算最终总百分比概率
        total_p = np.exp(best_score) * 100
        
        return final_9.sort_values(by='match_no'), round(total_p, 4)

    def select_9_final_logic(self, df):
        df = df.copy()

        # --- 关键修正：确保场次是数字类型，防止出现 1, 10, 11, 2 的排序错误 ---
        df['match_no'] = df['match_no'].astype(int)
        # 计算辅助列
        df['S1'] = df[['胜%', '平%', '负%']].max(axis=1)
        df['S2'] = df[['胜%', '平%', '负%']].apply(lambda x: sum(sorted(x, reverse=True)[:2]), axis=1)
        
        # 映射表：列名转选号
        col_to_tag = {"胜%": "3", "平%": "1", "负%": "0"}

        # 提取推荐选号的辅助函数
        def get_tags(row, is_double):
            # 将胜平负概率排序
            probs = {"3": row['胜%'], "1": row['平%'], "0": row['负%']}
            sorted_tags = sorted(probs, key=probs.get, reverse=True)
            return "/".join(sorted_tags[:2]) if is_double else sorted_tags[0]

        # --- 执行 4-3-2 筛选 ---
        df_sorted_s1 = df.sort_values(by='S1', ascending=False)
        
        # 1. 核心 4 场 (单选)
        part1 = df_sorted_s1.head(4).copy()
        part1['建议'] = part1.apply(lambda r: get_tags(r, False), axis=1)
        part1['投法'] = '单选'

        # 2. 剩余 10 场中 S2 前 3 (双选)
        remaining_10 = df_sorted_s1.iloc[4:].copy()
        part2 = remaining_10.sort_values(by='S2', ascending=False).head(3).copy()
        part2['建议'] = part2.apply(lambda r: get_tags(r, True), axis=1)
        part2['投法'] = '双选'

        # 3. 剩余 7 场中 S1 前 2 (单选)
        already_ids = part2['match_no'].tolist()
        part3 = remaining_10[~remaining_10['match_no'].isin(already_ids)].sort_values(by='S1', ascending=False).head(2).copy()
        part3['建议'] = part3.apply(lambda r: get_tags(r, False), axis=1)
        part3['投法'] = '单选'

        # 合并并按 match_no 增序排序
        final_9 = pd.concat([part1, part2, part3]).sort_values(by='match_no', ascending=True)
        
        # 计算总概率
        total_p = 1.0
        for _, row in final_9.iterrows():
            total_p *= (row['S2'] if row['投法'] == '双选' else row['S1']) / 100
            
        return final_9, round(total_p * 100, 4)