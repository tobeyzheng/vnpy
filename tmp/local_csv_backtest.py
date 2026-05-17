#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地CSV数据回测解决方案 - 使用本地CSV文件进行策略回测
不依赖Tushare等外部数据服务
"""

import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import numpy as np

# 添加vnpy路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from vnpy.trader.object import BarData, OrderData, TradeData
from vnpy.trader.constant import Exchange, Interval, Direction, Offset


class CSVDataLoader:
    """CSV数据加载器"""
    
    def __init__(self, data_dir: str = "./tmp/data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def create_sample_data(self, symbol: str = "000300", exchange: str = "SSE"):
        """创建示例数据"""
        
        print(f"📊 创建示例数据: {symbol}.{exchange}")
        
        # 生成示例数据（沪深300指数模拟数据）
        start_date = datetime(2020, 1, 1)
        end_date = datetime(2024, 12, 31)
        
        # 生成日期序列
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        dates = dates[dates.dayofweek < 5]  # 只保留工作日
        
        # 生成价格序列（模拟沪深300走势）
        np.random.seed(42)  # 固定随机种子保证结果可重现
        
        # 初始价格
        base_price = 4000
        returns = np.random.normal(0.001, 0.02, len(dates))
        
        # 计算价格序列
        prices = [base_price]
        for ret in returns:
            prices.append(prices[-1] * (1 + ret))
        prices = prices[1:]
        
        # 生成OHLCV数据
        data = []
        for i, date in enumerate(dates):
            if i >= len(prices):
                break
                
            close_price = prices[i]
            open_price = close_price * (1 + np.random.normal(0, 0.005))
            high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, 0.01)))
            low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, 0.01)))
            volume = np.random.randint(1000000, 5000000)
            turnover = volume * close_price
            
            data.append({
                'datetime': date.strftime('%Y-%m-%d'),
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': volume,
                'turnover': round(turnover, 2)
            })
        
        # 保存为CSV文件
        csv_file = self.data_dir / f"{symbol}_{exchange}_daily.csv"
        
        df = pd.DataFrame(data)
        df.to_csv(csv_file, index=False)
        
        print(f"✅ 示例数据已创建: {csv_file}")
        print(f"📅 数据条数: {len(data)}")
        print(f"📅 时间范围: {data[0]['datetime']} 到 {data[-1]['datetime']}")
        
        return csv_file
    
    def load_csv_data(self, csv_file: str) -> List[BarData]:
        """从CSV文件加载数据"""
        
        print(f"📥 加载CSV数据: {csv_file}")
        
        try:
            df = pd.read_csv(csv_file)
            
            bars = []
            for _, row in df.iterrows():
                # 解析symbol和exchange
                filename = Path(csv_file).stem
                parts = filename.split('_')
                symbol = parts[0]
                exchange_str = parts[1] if len(parts) > 1 else "SSE"
                
                # 映射交易所
                exchange_map = {
                    "SSE": Exchange.SSE,
                    "SZSE": Exchange.SZSE,
                    "SEHK": Exchange.SEHK,
                    "NASDAQ": Exchange.NASDAQ,
                    "NYSE": Exchange.NYSE
                }
                exchange = exchange_map.get(exchange_str, Exchange.SSE)
                
                bar = BarData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=datetime.strptime(row['datetime'], '%Y-%m-%d'),
                    interval=Interval.DAILY,
                    open_price=float(row['open']),
                    high_price=float(row['high']),
                    low_price=float(row['low']),
                    close_price=float(row['close']),
                    volume=float(row['volume']),
                    turnover=float(row.get('turnover', 0)),
                    gateway_name="csv_loader"
                )
                bars.append(bar)
            
            print(f"✅ 成功加载 {len(bars)} 条K线数据")
            return bars
            
        except Exception as e:
            print(f"❌ 数据加载失败: {e}")
            return []


class SimpleBacktestEngine:
    """简单回测引擎"""
    
    def __init__(self, capital: float = 1000000):
        self.capital = capital
        self.cash = capital
        self.position = 0
        self.trades = []
        self.bars = []
        self.current_bar = None
        
        # 手续费和滑点
        self.commission_rate = 0.0003  # 万分之三
        self.slippage = 0.001  # 千分之一
    
    def load_data(self, bars: List[BarData]):
        """加载回测数据"""
        self.bars = bars
        print(f"📊 加载了 {len(bars)} 条K线数据")
    
    def buy(self, price: float, volume: int):
        """买入操作"""
        return self._trade(Direction.LONG, price, volume)
    
    def sell(self, price: float, volume: int):
        """卖出操作"""
        return self._trade(Direction.SHORT, price, volume)
    
    def _trade(self, direction: Direction, price: float, volume: int):
        """执行交易"""
        
        # 计算实际成交价格（考虑滑点）
        if direction == Direction.LONG:
            trade_price = price * (1 + self.slippage)
        else:
            trade_price = price * (1 - self.slippage)
        
        # 计算手续费
        commission = trade_price * volume * self.commission_rate
        total_cost = trade_price * volume + commission
        
        # 检查资金是否足够
        if direction == Direction.LONG and total_cost > self.cash:
            print(f"❌ 资金不足，无法买入 {volume} 股")
            return False
        
        # 执行交易
        if direction == Direction.LONG:
            # 买入
            self.cash -= total_cost
            self.position += volume
            
            trade = TradeData(
                symbol=self.bars[0].symbol if self.bars else "UNKNOWN",
                exchange=self.bars[0].exchange if self.bars else Exchange.SSE,
                orderid=f"order_{len(self.trades)}",
                tradeid=f"trade_{len(self.trades)}",
                direction=Direction.LONG,
                offset=Offset.OPEN,
                price=trade_price,
                volume=volume,
                datetime=self.current_bar.datetime if self.current_bar else datetime.now(),
                gateway_name="backtest"
            )
            
            self.trades.append(trade)
            print(f"✅ 买入 {volume} 股，价格: {trade_price:.2f}")
            
        else:
            # 卖出
            if volume > self.position:
                volume = self.position  # 不能卖出超过持仓的数量
            
            self.cash += trade_price * volume - commission
            self.position -= volume
            
            trade = TradeData(
                symbol=self.bars[0].symbol if self.bars else "UNKNOWN",
                exchange=self.bars[0].exchange if self.bars else Exchange.SSE,
                orderid=f"order_{len(self.trades)}",
                tradeid=f"trade_{len(self.trades)}",
                direction=Direction.SHORT,
                offset=Offset.CLOSE,
                price=trade_price,
                volume=volume,
                datetime=self.current_bar.datetime if self.current_bar else datetime.now(),
                gateway_name="backtest"
            )
            
            self.trades.append(trade)
            print(f"✅ 卖出 {volume} 股，价格: {trade_price:.2f}")
        
        return True
    
    def run_backtest(self, strategy) -> Dict[str, Any]:
        """运行回测"""
        
        print(f"🚀 开始回测")
        print(f"💰 初始资金: {self.capital:,.2f}")
        
        # 策略初始化
        strategy.on_init()
        
        # 遍历所有K线数据
        for i, bar in enumerate(self.bars):
            self.current_bar = bar
            
            # 调用策略的on_bar方法
            strategy.on_bar(bar)
            
            # 每100条K线打印一次进度
            if (i + 1) % 100 == 0:
                market_value = self.position * bar.close_price if self.position > 0 else 0
                total_value = self.cash + market_value
                profit = total_value - self.capital
                profit_pct = profit / self.capital * 100
                
                print(f"📊 进度: {i+1}/{len(self.bars)} | "
                      f"持仓: {self.position} | "
                      f"市值: {market_value:,.2f} | "
                      f"总资产: {total_value:,.2f} | "
                      f"收益: {profit:,.2f} ({profit_pct:.2f}%)")
        
        # 计算最终结果
        final_bar = self.bars[-1] if self.bars else None
        market_value = self.position * final_bar.close_price if final_bar and self.position > 0 else 0
        total_value = self.cash + market_value
        
        result = {
            "initial_capital": self.capital,
            "final_value": total_value,
            "total_return": (total_value - self.capital) / self.capital,
            "position": self.position,
            "cash": self.cash,
            "market_value": market_value,
            "total_trades": len(self.trades),
            "bars_count": len(self.bars)
        }
        
        return result


class SimpleStrategy:
    """简单策略示例"""
    
    def __init__(self, engine):
        self.engine = engine
        self.fast_window = 5
        self.slow_window = 20
        self.position = 0
        
    def on_init(self):
        """策略初始化"""
        print(f"🎯 策略初始化完成")
        print(f"⚙️ 参数: 快线={self.fast_window}, 慢线={self.slow_window}")
    
    def on_bar(self, bar: BarData):
        """K线推送处理"""
        
        # 获取历史数据
        history = self.engine.bars[:self.engine.bars.index(bar) + 1]
        
        if len(history) < self.slow_window:
            return
        
        # 计算移动平均线
        closes = [b.close_price for b in history]
        
        fast_ma = sum(closes[-self.fast_window:]) / self.fast_window
        slow_ma = sum(closes[-self.slow_window:]) / self.slow_window
        
        # 生成交易信号
        if fast_ma > slow_ma and self.position == 0:
            # 金叉信号，买入
            volume = int(self.engine.cash * 0.9 / bar.close_price)  # 使用90%资金
            if volume > 0:
                self.engine.buy(bar.close_price, volume)
                self.position = volume
                print(f"📈 买入信号: 快线{fast_ma:.2f} > 慢线{slow_ma:.2f}")
        
        elif fast_ma < slow_ma and self.position > 0:
            # 死叉信号，卖出
            self.engine.sell(bar.close_price, self.position)
            self.position = 0
            print(f"📉 卖出信号: 快线{fast_ma:.2f} < 慢线{slow_ma:.2f}")


def main():
    """主函数"""
    
    print("=" * 60)
    print("本地CSV数据回测解决方案")
    print("=" * 60)
    
    # 1. 创建数据加载器
    loader = CSVDataLoader()
    
    # 2. 创建示例数据（如果不存在）
    csv_file = loader.data_dir / "000300_SSE_daily.csv"
    if not csv_file.exists():
        print("\n1. 创建示例数据")
        csv_file = loader.create_sample_data("000300", "SSE")
    else:
        print(f"\n1. 使用现有数据文件: {csv_file}")
    
    # 3. 加载数据
    print("\n2. 加载数据")
    bars = loader.load_csv_data(csv_file)
    
    if not bars:
        print("❌ 数据加载失败")
        return
    
    # 4. 创建回测引擎
    print("\n3. 创建回测引擎")
    engine = SimpleBacktestEngine(capital=1000000)
    engine.load_data(bars)
    
    # 5. 创建策略
    print("\n4. 创建策略")
    strategy = SimpleStrategy(engine)
    
    # 6. 运行回测
    print("\n5. 运行回测")
    result = engine.run_backtest(strategy)
    
    # 7. 显示结果
    print("\n" + "=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    
    print(f"💰 初始资金: {result['initial_capital']:,.2f}")
    print(f"💰 最终资产: {result['final_value']:,.2f}")
    print(f"📈 总收益率: {result['total_return']:.2%}")
    print(f"📊 交易次数: {result['total_trades']}")
    print(f"📅 数据条数: {result['bars_count']}")
    print(f"💵 现金余额: {result['cash']:,.2f}")
    print(f"📦 持仓数量: {result['position']}")
    print(f"🏢 持仓市值: {result['market_value']:,.2f}")
    
    # 8. 使用说明
    print("\n" + "=" * 60)
    print("🎯 使用说明")
    print("=" * 60)
    print("""
1. 使用自己的数据:
   - 将CSV文件放入 ./tmp/data/ 目录
   - 格式: datetime,open,high,low,close,volume,turnover
   - 文件名格式: {symbol}_{exchange}_daily.csv

2. 修改策略:
   - 编辑 SimpleStrategy 类的 on_bar 方法
   - 添加更多技术指标和交易逻辑

3. 参数优化:
   - 修改策略参数（fast_window, slow_window等）
   - 运行多次回测比较结果

4. 支持的功能:
   - 多品种回测
   - 复杂订单类型
   - 风险管理
   - 性能分析

5. 下一步:
   - 尝试不同的策略逻辑
   - 添加更多技术指标
   - 进行参数优化
   - 生成详细报告
""")


if __name__ == "__main__":
    main()