# -*- coding: utf-8 -*-
"""L5 · 门面层：用户代码与外层（Runner / Web 后端）唯一入口。

门面可以随业务演进加方法，家族契约不能动——这是分层能长期成立的前提。
两个门面：

* ``instruments``：示波器等仪器，``open_scope`` / ``Scope``；
* ``camera``：工业相机，``open_camera`` / ``Camera``。

``from ate_drivers.api import Scope, open_scope`` 与 ``from ate_drivers import Scope``
等价；两种写法都指向同一个类。
"""

from .camera import Camera  # noqa: F401
from .instruments import Scope  # noqa: F401
from ..assembly.cameras import open_camera  # noqa: F401  装配层实现，门面统一出口
from ..assembly.scopes import open_scope  # noqa: F401  同上
