"""
Trend Explosion Discovery (TED) v1
进攻型标池发现策略 - 专门寻找类似闪迪、美光等强趋势暴涨标的
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class TrendExplosionDiscovery:
    """进攻型标池发现器"""

    def __init__(self, base_path: str = "/projects/vnpy"):
        self.base_path = base_path
        self.log_path = os.path.join(base_path, "log")
        self.state_path = os.path.join(base_path, "state", "runs")

    def load_knot_4dim_results(self) -> Dict[str, List[str]]:
        """加载Knot四维研究结果"""
        try:
            # 查找最新的Knot四维研究结果
            knot_logs = []
            if os.path.exists(self.log_path):
                for date_dir in sorted(os.listdir(self.log_path), reverse=True):
                    knot_file = os.path.join(self.log_path, date_dir, "knot_4dim_us.json")
                    if os.path.exists(knot_file):
                        knot_logs.append(knot_file)

            if not knot_logs:
                logger.warning("未找到Knot四维研究结果")
                return {}

            # 使用最新的结果
            latest_file = knot_logs[0]
            with open(latest_file, 'r') as f:
                data = json.load(f)

            return {
                "technical": data.get("technical", []),
                "fundamental": data.get("fundamental", []),
                "capital_flow": data.get("capital_flow", []),
                "event_driven": data.get("event_driven", [])
            }

        except Exception as e:
            logger.error(f"加载Knot四维结果失败: {e}")
            return {}

    def load_dynamic_candidates(self) -> List[Dict[str, Any]]:
        """加载动态候选池"""
        try:
            dynamic_file = os.path.join(self.state_path, "candidate_inputs.dynamic.us.json")
            if not os.path.exists(dynamic_file):
                logger.warning("未找到动态候选文件")
                return []

            with open(dynamic_file, 'r') as f:
                data = json.load(f)

            return data.get("items", [])

        except Exception as e:
            logger.error(f"加载动态候选失败: {e}")
            return []

    def calculate_aeos_score(self, candidate: Dict[str, Any]) -> float:
        """计算进攻型机会评分 (Aggressive Explosion Opportunity Score)"""

        # 基础评分组件
        trend_score = candidate.get("trend_score", 0)
        relative_strength_score = candidate.get("relative_strength_score", 0)
        flow_score = candidate.get("flow_score", 0)
        event_score = candidate.get("event_score", 0)
        liquidity_score = candidate.get("liquidity_score", 0)

        # 进攻型权重分配
        weights = {
            "breakout_trend": 0.30,      # 突破趋势
            "relative_strength": 0.20,   # 相对强弱
            "flow_participation": 0.15,  # 资金参与
            "event_freshness": 0.15,     # 事件新鲜度
            "theme_leadership": 0.10,    # 主题领导力
            "liquidity_quality": 0.10,   # 流动性质量
        }

        # 风险调整因子
        risk_adjustments = {
            "exhaustion_risk": -0.10,    # 衰竭风险
            "structural_risk": -0.10,    # 结构性风险
        }

        # 计算基础得分
        base_score = (
            trend_score * weights["breakout_trend"] +
            relative_strength_score * weights["relative_strength"] +
            flow_score * weights["flow_participation"] +
            event_score * weights["event_freshness"] +
            (trend_score * 0.5 + relative_strength_score * 0.5) * weights["theme_leadership"] +
            liquidity_score * weights["liquidity_quality"]
        )

        # 风险调整
        quote = candidate.get("quote", {})
        change_pct = quote.get("change_pct", 0)
        turnover_ratio = quote.get("turnover_ratio", 0)

        # 衰竭风险：涨幅过大、换手过高
        exhaustion_risk = 0
        if change_pct > 50:  # 单日涨幅超过50%
            exhaustion_risk += 0.3
        elif change_pct > 30:
            exhaustion_risk += 0.2
        elif change_pct > 15:
            exhaustion_risk += 0.1

        if turnover_ratio > 20:  # 换手率过高
            exhaustion_risk += 0.2
        elif turnover_ratio > 10:
            exhaustion_risk += 0.1

        # 结构性风险：小市值、低流动性
        structural_risk = 0
        market_cap = quote.get("market_cap", 0)
        if market_cap < 1e9:  # 市值小于10亿
            structural_risk += 0.3
        elif market_cap < 5e9:
            structural_risk += 0.1

        # 应用风险调整
        final_score = base_score + (
            exhaustion_risk * risk_adjustments["exhaustion_risk"] +
            structural_risk * risk_adjustments["structural_risk"]
        )

        return max(0, min(1, final_score))

    def filter_aggressive_candidates(self, candidates: List[Dict[str, Any]],
                                   top_n: int = 15) -> List[Dict[str, Any]]:
        """筛选进攻型候选标的"""

        filtered_candidates = []

        for candidate in candidates:
            # 基础过滤条件
            quote = candidate.get("quote", {})

            # 必须条件
            if not quote:
                continue

            # 流动性过滤
            market_cap = quote.get("market_cap", 0)
            turnover = quote.get("turnover", 0)

            if market_cap < 5e9:  # 市值小于50亿
                continue

            if turnover < 1e7:  # 成交额小于1000万
                continue

            # 趋势过滤
            change_pct = quote.get("change_pct", 0)
            if change_pct < 2:  # 涨幅小于2%
                continue

            # 计算AEOS评分
            aeos_score = self.calculate_aeos_score(candidate)

            # 添加评分信息
            candidate["aeos_score"] = aeos_score
            candidate["aeos_components"] = {
                "trend": candidate.get("trend_score", 0),
                "relative_strength": candidate.get("relative_strength_score", 0),
                "flow": candidate.get("flow_score", 0),
                "event": candidate.get("event_score", 0),
                "liquidity": candidate.get("liquidity_score", 0)
            }

            filtered_candidates.append(candidate)

        # 按AEOS评分排序
        filtered_candidates.sort(key=lambda x: x.get("aeos_score", 0), reverse=True)

        return filtered_candidates[:top_n]

    def categorize_aggressive_pool(self, candidates: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """将候选池分类为核心池、观察池、备用池"""

        categorized = {
            "core_pool": [],      # 核心进攻池 (4-6只)
            "watch_pool": [],     # 观察补位池 (4-6只)
            "reserve_pool": []    # 主题备用池 (2-4只)
        }

        if not candidates:
            return categorized

        # 按AEOS评分分类
        for i, candidate in enumerate(candidates):
            aeos_score = candidate.get("aeos_score", 0)

            if i < 6 and aeos_score >= 0.7:
                categorized["core_pool"].append(candidate)
            elif i < 12 and aeos_score >= 0.6:
                categorized["watch_pool"].append(candidate)
            else:
                categorized["reserve_pool"].append(candidate)

        return categorized

    def generate_aggressive_pool_report(self, categorized_pool: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """生成进攻型标池报告"""

        report = {
            "schema_version": "ted_v1",
            "generated_at": datetime.now().isoformat(),
            "strategy": "trend_explosion_discovery",
            "pool_summary": {
                "total_candidates": sum(len(pool) for pool in categorized_pool.values()),
                "core_pool_count": len(categorized_pool["core_pool"]),
                "watch_pool_count": len(categorized_pool["watch_pool"]),
                "reserve_pool_count": len(categorized_pool["reserve_pool"])
            },
            "core_pool": [],
            "watch_pool": [],
            "reserve_pool": []
        }

        # 格式化每个池子的标的
        for pool_type, candidates in categorized_pool.items():
            for candidate in candidates:
                quote = candidate.get("quote", {})
                report[pool_type].append({
                    "symbol": candidate.get("symbol", ""),
                    "name": candidate.get("name", ""),
                    "aeos_score": candidate.get("aeos_score", 0),
                    "price": quote.get("price", 0),
                    "change_pct": quote.get("change_pct", 0),
                    "market_cap": quote.get("market_cap", 0),
                    "turnover": quote.get("turnover", 0),
                    "rationale": candidate.get("rationale", ""),
                    "risk_level": candidate.get("risk_level", "medium")
                })

        return report

    def discover_aggressive_pool(self) -> Dict[str, Any]:
        """主发现流程"""

        logger.info("开始进攻型标池发现流程...")

        # 1. 加载Knot四维研究结果
        knot_results = self.load_knot_4dim_results()
        logger.info(f"加载Knot四维结果: {len(knot_results)}个维度")

        # 2. 加载动态候选池
        dynamic_candidates = self.load_dynamic_candidates()
        logger.info(f"加载动态候选: {len(dynamic_candidates)}个标的")

        # 3. 筛选进攻型候选
        aggressive_candidates = self.filter_aggressive_candidates(dynamic_candidates)
        logger.info(f"筛选进攻型候选: {len(aggressive_candidates)}个标的")

        # 4. 分类标池
        categorized_pool = self.categorize_aggressive_pool(aggressive_candidates)

        # 5. 生成报告
        report = self.generate_aggressive_pool_report(categorized_pool)

        logger.info("进攻型标池发现完成")

        return report


def main():
    """主函数 - 用于测试"""
    ted = TrendExplosionDiscovery()
    report = ted.discover_aggressive_pool()

    # 打印报告摘要
    print("=== 进攻型标池发现报告 ===")
    print(f"生成时间: {report['generated_at']}")
    print(f"策略: {report['strategy']}")
    print(f"总候选数: {report['pool_summary']['total_candidates']}")
    print(f"核心池: {report['pool_summary']['core_pool_count']}只")
    print(f"观察池: {report['pool_summary']['watch_pool_count']}只")
    print(f"备用池: {report['pool_summary']['reserve_pool_count']}只")

    # 打印核心池详情
    if report['core_pool']:
        print("\n=== 核心进攻池 ===")
        for stock in report['core_pool']:
            print(f"{stock['symbol']} - {stock['name']}")
            print(f"  AEOS评分: {stock['aeos_score']:.3f}, 涨幅: {stock['change_pct']:.1f}%")
            print(f"  市值: {stock['market_cap']/1e9:.1f}B, 成交额: {stock['turnover']/1e6:.1f}M")


if __name__ == "__main__":
    main()