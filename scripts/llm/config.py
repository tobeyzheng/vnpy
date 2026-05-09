"""
LLM模块配置
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class LLMConfig:
    """LLM配置类"""
    # KnotAgent配置
    knot_agent_enabled: bool = True
    knot_agent_timeout: int = 30
    knot_agent_max_tokens: int = 2000

    # 分析配置
    default_timeframe: str = "1d"
    include_technical: bool = True
    include_capital: bool = False
    include_derivatives: bool = False

    # 缓存配置
    cache_enabled: bool = True
    cache_ttl: int = 300  # 5分钟

    # 支持的股票市场
    supported_markets: List[str] = None

    def __post_init__(self):
        if self.supported_markets is None:
            self.supported_markets = ["US", "HK", "SH", "SZ"]


# 默认配置实例
default_config = LLMConfig()


@dataclass
class AnalysisPromptTemplates:
    """分析提示词模板"""

    @staticmethod
    def get_stock_analysis_template() -> str:
        """获取股票分析模板"""
        return """
请作为专业的量化交易分析师，对以下股票进行综合分析：

股票: {symbol}
市场: {market}
时间框架: {timeframe}

市场环境:
- 大盘基准: {benchmark}
- 市场情绪: {sentiment}

价格数据:
- 最新价: {price}
- 涨跌幅: {change_pct}%
- 成交量: {volume}

{technical_indicators}

请给出详细的操作建议，包括：
1. 技术面分析
2. 资金面情况
3. 短期交易策略（1-3天）
4. 中期投资观点（1-4周）
5. 关键价位（支撑/阻力）
6. 风险管理建议
7. 仓位配置建议

请用中文回答，保持客观专业。
"""

    @staticmethod
    def get_technical_indicators_section() -> str:
        """获取技术指标部分模板"""
        return """
技术指标:
- RSI: {rsi} (超买>70, 超卖<30)
- MACD: {macd}
- 均线: {ma}
- 布林带: {boll}
- 成交量指标: {volume_indicator}
"""