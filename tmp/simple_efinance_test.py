#!/usr/bin/env python3
"""
简单测试Efinance数据服务
"""

import sys
import os

# 添加当前目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_efinance_direct():
    """直接测试Efinance"""
    print("🧪 直接测试Efinance...")
    
    try:
        import efinance as ef
        print("✅ Efinance导入成功")
        
        # 测试A股数据
        print("\n📊 测试A股数据获取...")
        df = ef.stock.get_quote_history('600519', beg='20240101', end='20240110', klt=101)
        print(f"✅ 获取到 {len(df)} 条数据")
        if not df.empty:
            print("前3行数据:")
            print(df.head(3))
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_efinance_datafeed():
    """测试Efinance数据服务"""
    print("\n🧪 测试Efinance数据服务...")
    
    try:
        from vnpy_efinance.efinance_datafeed import EfinanceDatafeed
        print("✅ EfinanceDatafeed导入成功")
        
        datafeed = EfinanceDatafeed()
        if datafeed.init(output=print):
            print("✅ 数据服务初始化成功")
        else:
            print("❌ 数据服务初始化失败")
            return False
            
        return True
        
    except Exception as e:
        print(f"❌ 数据服务测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("Efinance数据服务测试")
    print("=" * 50)
    
    # 先测试直接调用
    if test_efinance_direct():
        print("\n" + "=" * 50)
        print("✅ 直接调用测试成功")
        print("=" * 50)
    
    # 再测试数据服务
    if test_efinance_datafeed():
        print("\n" + "=" * 50)
        print("✅ 数据服务测试成功")
        print("=" * 50)
    
    print("\n🎯 测试完成")