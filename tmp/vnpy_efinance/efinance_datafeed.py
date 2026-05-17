"""
Efinance datafeed implementation for VeighNa.
"""

import efinance as ef
from datetime import datetime, timedelta
from typing import List
from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import HistoryRequest, BarData, TickData
from vnpy.trader.constant import Exchange, Interval


class EfinanceDatafeed(BaseDatafeed):
    """Efinance datafeed implementation"""

    def init(self, output=None) -> bool:
        """Initialize Efinance datafeed"""
        try:
            # Test if efinance is available by importing it
            import efinance as ef
            # Simple test to verify efinance is working
            _ = ef.__name__
            if output:
                output("✅ Efinance数据服务初始化成功")
            return True
        except Exception as e:
            if output:
                output(f"❌ Efinance数据服务初始化失败: {e}")
            return False

    def query_bar_history(self, req: HistoryRequest, output=None) -> List[BarData]:
        """Query bar history data from Efinance"""
        try:
            import efinance as ef
            
            # Convert interval to Efinance compatible format
            freq_map = {
                Interval.MINUTE: 1,
                Interval.HOUR: 60, 
                Interval.DAILY: 101,
                Interval.WEEKLY: 102,
                Interval.MONTHLY: 103
            }
            
            freq = freq_map.get(req.interval, 101)
            
            # Get symbol and exchange info
            # 直接使用req.symbol和req.exchange，不进行复杂的解析
            symbol = req.symbol
            exchange = req.exchange
            
            # Convert exchange to Efinance market code
            market_map = {
                Exchange.SSE: "sh",
                Exchange.SZSE: "sz",
                Exchange.BSE: "bj",
                Exchange.HKFE: "hk",
                Exchange.NYSE: "us",
                Exchange.NASDAQ: "us"
            }
            
            market = market_map.get(exchange, "sh")
            
            # Format symbol for Efinance
            # efinance 使用东方财富接口，A 股直接传 6 位纯代码即可，无需 sh/sz 前缀；
            # 加前缀（如 "sh600519"）会触发名称模糊匹配，可能引发请求阻塞或空结果。
            if market in ["sh", "sz", "bj"]:
                efinance_symbol = symbol
            elif market == "hk":
                # 港股：补零到 5 位，不加 .HK 后缀
                efinance_symbol = symbol.zfill(5)
            else:
                # 美股：直接使用 ticker
                efinance_symbol = symbol
            
            # Calculate date range
            start_date = req.start.strftime("%Y%m%d")
            end_date = req.end.strftime("%Y%m%d")
            
            if output:
                output(f"正在从Efinance获取数据: {efinance_symbol}, {freq}, {start_date}-{end_date}")
            
            # Get data from Efinance
            # 注意：efinance 的真实参数名是 stock_codes（接受 str 或 List[str]），
            # 这里使用位置参数避免误用关键字。
            df = ef.stock.get_quote_history(
                efinance_symbol,
                beg=start_date,
                end=end_date,
                klt=freq,
                suppress_error=True,
            )
            
            if df.empty:
                if output:
                    output(f"❌ 未获取到数据: {efinance_symbol}")
                return []
            
            # Convert to BarData objects
            bars = []
            for _, row in df.iterrows():
                # 检查数据列是否存在
                date_str = str(row.get('日期', row.get('date', '')))
                if not date_str:
                    continue
                    
                bar = BarData(
                    symbol=req.symbol,
                    exchange=exchange,
                    datetime=datetime.strptime(date_str, "%Y-%m-%d"),
                    interval=req.interval,
                    open_price=float(row.get('开盘', row.get('open', 0))),
                    high_price=float(row.get('最高', row.get('high', 0))),
                    low_price=float(row.get('最低', row.get('low', 0))),
                    close_price=float(row.get('收盘', row.get('close', 0))),
                    volume=float(row.get('成交量', row.get('volume', 0))),
                    turnover=float(row.get('成交额', row.get('turnover', 0))),
                    gateway_name="Efinance"
                )
                bars.append(bar)
            
            if output:
                output(f"✅ 成功获取 {len(bars)} 条K线数据")
            
            return bars
            
        except Exception as e:
            if output:
                output(f"❌ 获取K线数据失败: {type(e).__name__}: {e}")
            else:
                import traceback
                traceback.print_exc()
            return []

    def query_tick_history(self, req: HistoryRequest, output=None) -> List[TickData]:
        """Query tick history data from Efinance"""
        if output:
            output("⚠️ Efinance暂不支持Tick级别历史数据查询")
        return []