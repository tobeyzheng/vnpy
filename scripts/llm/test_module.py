#!/usr/bin/env python3
"""
LLM模块测试脚本
"""

import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

def test_basic_imports():
    """测试基本导入"""
    try:
        from scripts.llm import __version__
        from scripts.llm.config import LLMConfig, default_config
        print("✓ 基本导入成功")
        print(f"版本: {__version__}")
        print(f"默认配置: {default_config}")
        return True
    except ImportError as e:
        print(f"✗ 导入失败: {e}")
        return False

def test_analyze_stock_script():
    """测试分析脚本"""
    try:
        # 模拟命令行参数
        class Args:
            symbol = "US.TSLA"
            timeframe = "1d"
            include_technical = True
            include_capital = False
            include_derivatives = False
            verbose = True

        from scripts.llm.analyze_stock_with_llm import get_market_context, generate_analysis_prompt

        # 测试市场上下文获取
        context = get_market_context("US.TSLA", "1d")
        assert 'symbol' in context
        assert 'market' in context
        print("✓ 市场上下文获取成功")

        # 测试提示词生成
        args = Args()
        prompt = generate_analysis_prompt(context, args)
        assert "US.TSLA" in prompt
        assert "操作建议" in prompt
        print("✓ 提示词生成成功")

        return True
    except Exception as e:
        print(f"✗ 分析脚本测试失败: {e}")
        return False

def test_anomaly_detector():
    """测试异动检测"""
    try:
        from scripts.llm.anomaly_detector import detect_anomalies, format_anomalies_for_prompt

        # 测试异动检测（模拟模式）
        anomalies = detect_anomalies("US.TSLA", ['technical'])
        assert isinstance(anomalies, dict)
        print("✓ 异动检测成功")

        # 测试提示词格式化
        formatted = format_anomalies_for_prompt(anomalies)
        assert isinstance(formatted, str)
        print("✓ 提示词格式化成功")

        return True
    except Exception as e:
        print(f"✗ 异动检测测试失败: {e}")
        return False

def test_config():
    """测试配置"""
    try:
        from scripts.llm.config import LLMConfig, AnalysisPromptTemplates

        # 测试配置类
        config = LLMConfig()
        assert config.knot_agent_enabled == True
        assert config.default_timeframe == "1d"
        print("✓ 配置类测试成功")

        # 测试提示词模板
        template = AnalysisPromptTemplates.get_stock_analysis_template()
        assert "{symbol}" in template
        assert "操作建议" in template
        print("✓ 提示词模板测试成功")

        return True
    except Exception as e:
        print(f"✗ 配置测试失败: {e}")
        return False

def main():
    """运行所有测试"""
    print("运行LLM模块测试...\n")

    tests = [
        test_basic_imports,
        test_config,
        test_analyze_stock_script,
        test_anomaly_detector
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1
        print()

    print(f"测试结果: {passed}/{total} 通过")

    if passed == total:
        print("🎉 所有测试通过!")
        return 0
    else:
        print("❌ 部分测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())