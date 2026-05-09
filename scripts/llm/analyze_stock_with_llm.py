#!/usr/bin/env python3
"""
股票分析与操作建议脚本

基于最新价格、走势、异动和大盘形势，调用KnotAgent生成操作建议
"""

import argparse
import sys
import os
from typing import Dict, Any, Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from services.knot_runtime.knot_agent import KnotAgent
    from services.evaluation_hub.market_analyzer import MarketAnalyzer
    from services.evaluation_hub.stock_evaluator import StockEvaluator
except ImportError:
    print("警告: 无法导入KnotAgent相关模块，请确保服务模块已正确配置")
    KnotAgent = None
    MarketAnalyzer = None
    StockEvaluator = None


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='基于LLM的股票分析与操作建议生成')
    parser.add_argument('symbol', help='股票代码 (格式: US.TSLA, HK.00700)')
    parser.add_argument('--timeframe', '-t', default='1d',
                       choices=['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'],
                       help='时间框架，默认1d')
    parser.add_argument('--include-technical', action='store_true',
                       help='包含技术分析')
    parser.add_argument('--include-capital', action='store_true',
                       help='包含资金流分析')
    parser.add_argument('--include-derivatives', action='store_true',
                       help='包含衍生品分析')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='详细输出模式')

    return parser.parse_args()


def get_market_context(symbol: str, timeframe: str) -> Dict[str, Any]:
    """获取市场上下文信息"""
    context = {
        'symbol': symbol,
        'timeframe': timeframe,
        'price_data': {},
        'technical_indicators': {},
        'market_sentiment': {},
        'anomalies': []
    }

    # 这里应该集成实际的行情数据获取逻辑
    # 暂时使用模拟数据
    if symbol.startswith('US.'):
        context['market'] = '美股'
        context['benchmark'] = 'SPY'
    elif symbol.startswith('HK.'):
        context['market'] = '港股'
        context['benchmark'] = 'HSI'
    else:
        context['market'] = '未知市场'
        context['benchmark'] = ''

    return context


def generate_analysis_prompt(context: Dict[str, Any], args: argparse.Namespace) -> str:
    """生成分析提示词"""
    prompt = f"""
请分析以下股票的投资机会：

股票代码: {context['symbol']}
市场: {context['market']}
时间框架: {context['timeframe']}

当前市场环境:
- 大盘基准: {context['benchmark']}
- 整体市场情绪: {context.get('market_sentiment', {}).get('overall', '中性')}

价格信息:
- 最新价格: {context['price_data'].get('close', 'N/A')}
- 日内涨跌幅: {context['price_data'].get('change_pct', 'N/A')}%
- 成交量: {context['price_data'].get('volume', 'N/A')}

请基于以上信息，给出详细的操作建议，包括：
1. 短期交易策略（1-3天）
2. 中期投资观点（1-4周）
3. 关键支撑位和阻力位
4. 风险提示和止损建议
5. 仓位管理建议

请用中文回答，保持专业客观的分析态度。
"""

    if args.include_technical:
        tech_indicators = context.get('technical_indicators', {})
        prompt += f"""
技术指标:
- RSI: {tech_indicators.get('rsi', 'N/A')}
- MACD: {tech_indicators.get('macd', 'N/A')}
- 均线系统: {tech_indicators.get('ma', 'N/A')}
"""

    return prompt


def main():
    """主函数"""
    args = parse_arguments()

    print(f"开始分析股票: {args.symbol}")
    print(f"时间框架: {args.timeframe}")

    # 获取市场上下文
    context = get_market_context(args.symbol, args.timeframe)

    if args.verbose:
        print(f"市场上下文: {context}")

    # 生成分析提示词
    prompt = generate_analysis_prompt(context, args)

    if args.verbose:
        print(f"\n生成的提示词:\n{prompt}")

    # 调用KnotAgent
    try:
        if KnotAgent:
            agent = KnotAgent()
            response = agent.analyze_stock(prompt, context)

            print("\n" + "="*60)
            print("KnotAgent 操作建议:")
            print("="*60)
            print(response)
            print("="*60)
        else:
            # 模拟响应（开发测试用）
            print("\n" + "="*60)
            print("模拟操作建议 (KnotAgent未配置):")
            print("="*60)
            print(f"""
基于 {args.symbol} 的分析：

短期策略: 建议观望，等待明确方向突破
中期观点: 中性偏谨慎，关注关键技术位
支撑位: 需要实时数据计算
阻力位: 需要实时数据计算
风险提示: 市场波动较大，注意仓位控制

注: 此为模拟建议，请配置KnotAgent获取真实分析
""")
            print("="*60)

    except Exception as e:
        print(f"调用KnotAgent时出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()