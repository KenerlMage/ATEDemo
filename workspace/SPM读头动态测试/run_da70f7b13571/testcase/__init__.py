# -*- coding: utf-8 -*-
"""SPM 读头动态测试 TPS 的用例实现包

每个用例都是 `def 用例(ctx, **params)`：
* `ctx` 是公共 conftest 提供的运行上下文（装备信息 / 设备驱动 / 测试阈值 / 记录）
* 返回值可以是数值或字典；清单里 `checks` 会把字典字段映射到 `testconfig` 阈值上判定
* 也可以在用例内部用 `ctx.expect("阈值键", 值)` 直接判定
"""
