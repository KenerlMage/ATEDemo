# -*- coding: utf-8 -*-
"""示波器**通用顶层接口**——用户代码唯一入口（网口与串口一视同仁）。

四条硬约束

1. **按型号直连**：型号来自数据库设备档案，直接派发到对应厂商驱动；
   没有权重打分、没有关键字模糊匹配、没有兜底猜测。
2. **连接方式由档案决定**：网口（`host` + `port`）或串口（`serial_port` + `baudrate`）；
   两种参数同时出现在档案里又没写明 `interface` → 报 `E_CONFIG`，不替用户猜。
3. **不暴露原始命令**：本模块**一个仪器命令字符串都没有**——命令表、前导解析、
   缩放换算全部收在 `ate_drivers.family`（家族契约）与 `ate_drivers.vendors`（厂商包）内。
4. **换链路不改用例**：同一段业务代码，走网口与走串口只有档案不同。

典型用法

    from ate_drivers import open_scope

    # 网口真机
    with open_scope("MSO54", host="192.168.10.41", port=4000) as scope:
        scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
        scope.set_timebase(seconds_per_div=5e-4)
        scope.set_channel("CH1", volts_per_div=0.5, coupling="DC")
        wave = scope.capture(points=2000)
        for kind, item in scope.measure(("PK2PK", "FREQUENCY")).items():
            print(kind, item["value"], item["unit"])

    # 同一台设备的串口链路：只换连接参数，业务代码一行不改
    with open_scope("MSO54", serial_port="COM6", baudrate=115200) as scope:
        print(scope.identity())

    # 离线/联调（无实机）：不给任何连接参数即走仿真，动作与返回结构完全一致
    with open_scope("MXO44") as scope:
        print(scope.identity())

5. **后端可换**：连接方式（网口 / 串口）与传输后端（原生栈 / VISA）是两件事。
   默认 `backend="native"`（标准库 socket + pyserial，零依赖）；写 `backend="visa"` 即改用
   PyVISA（NI-VISA / Keysight VISA / pyvisa-py 任一实现）打开会话，命令与返回结构**完全不变**；
   `backend="auto"` 表示"装了 pyvisa 就走 VISA"。**实际生效的后端永远可从 `scope.backend` 查到**，
   声明的后端装不了时直接报 `E_CONFIG`，不静默退回。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .errors import ConfigurationError, DriverError
from .endpoint import DEFAULT_BACKEND, VISA, backends_of, endpoint_from, resolve_backend
from .family.scope import MEASURE_ITEMS, ScopeDriver
from .models import DeviceConfig
from .transport import visa_available

KIND = "oscilloscope"
MEASURE_ALIASES = {
    "VPP": "PK2PK", "峰峰值": "PK2PK", "PK2Pk": "PK2PK",
    "FREQ": "FREQUENCY", "频率": "FREQUENCY",
    "PERIOD": "PERIOD", "MEAN": "MEAN", "RMS": "RMS",
    "RISE": "RISE", "FALL": "FALL", "AMPLITUDE": "AMPLITUDE", "幅值": "AMPLITUDE",
}


class Scope:
    """一台网口示波器的通用顶层对象（型号直连，命令不外露）"""

    kind = KIND

    def __init__(self, model: Any, host: Optional[str] = None, port: Optional[int] = None, *,
                 endpoint: Any = None, interface: Any = None, serial_port: Any = None,
                 baudrate: Any = None, bytesize: Any = None, parity: Any = None,
                 stopbits: Any = None, mode: str = "auto", timeout: Optional[float] = 2.0,
                 transport: Any = None, alias: str = "", device_id: str = "", name: str = "",
                 verify_model: bool = True, open: bool = False, backend: Any = None) -> None:
        if not model:
            raise ConfigurationError("必须指定示波器型号",
                                    detail="型号来自设备档案的 model 字段（数据库设备表）")
        from . import factory

        self._entry = factory.entry_for(model)          # 未登记会在这一步直接报错
        self._cls = factory.driver_class(model)
        self._model = self._entry["model"]
        self._supported = tuple(self._entry.get("interfaces") or ())
        self._endpoint = endpoint if endpoint is not None else endpoint_from(
            None, interface=interface, host=host, port=port, serial_port=serial_port,
            baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits,
            timeout=timeout)
        if self._endpoint is not None and self._supported and self._endpoint.kind not in self._supported:
            raise ConfigurationError(
                "%s 不支持 %s 连接" % (self._model, self._endpoint.kind),
                detail="该型号声明的连接方式：%s（档案里的 interface 与端点参数要对齐）"
                       % (" / ".join(self._supported),))
        # 传输后端：显式 > 档案 > native；型号没声明的后端当场拒绝
        self._backends = tuple(self._entry.get("backends") or ()) or (DEFAULT_BACKEND,)
        self._backend_requested, self._backend = resolve_backend(
            backend, None, visa_available()
        )
        if self._backend not in self._backends:
            raise ConfigurationError(
                "%s 不支持 %s 后端" % (self._model, self._backend),
                detail="该型号声明的后端：" + " / ".join(self._backends),
            )
        self._mode = self._resolve_mode(mode)
        if self._mode == "real" and self._backend == VISA and not visa_available():
            raise ConfigurationError(
                "VISA 后端需要 pyvisa（当前环境未安装）",
                detail="pip install pyvisa pyvisa-py；仿真模式下不需要任何后端",
            )
        if self._mode == "real" and self._endpoint is None:
            raise ConfigurationError(
                "%s 的真机模式必须提供连接参数" % self._model,
                detail="网口：host + port（形如 host=\"192.168.10.41\", port=4000）；"
                       "串口：serial_port + baudrate（形如 serial_port=\"COM6\", baudrate=115200）")
        self._verify_model = bool(verify_model)
        self._verified: Optional[bool] = None
        if self._endpoint is not None:
            cfg = self._endpoint.to_device_config(
                model=self._model, vendor=self._entry["vendor"],
                category=self._entry["label"] or KIND, role=KIND,
                device_id=device_id or self._model, name=name or self._model,
                timeout=timeout if timeout is not None else 2.0)
        else:
            cfg = DeviceConfig(
                device_id=device_id or self._model, name=name or self._model,
                model=self._model, vendor=self._entry["vendor"],
                category=self._entry["label"] or KIND, role=KIND, interface="NONE",
                programmable=True, timeout=float(timeout if timeout is not None else 2.0))
        self._driver: ScopeDriver = self._cls(cfg, mode=self._mode, timeout=cfg.timeout,
                                              transport=transport, alias=alias,
                                              backend=self._backend)
        if open:
            self.connect()

    # ---------------------------------------------------------------- 元信息
    @property
    def model(self) -> str:
        """注册表里的正式型号名"""
        return self._model

    @property
    def vendor(self) -> str:
        return self._entry["vendor"]

    @property
    def label(self) -> str:
        return self._entry["label"]

    @property
    def interface(self) -> str:
        """当前连接方式：`LAN` / `SERIAL`；仿真时回落到驱动声明的首选方式"""
        if self._endpoint is not None:
            return self._endpoint.kind
        return (self._supported or ("",))[0]

    @property
    def interfaces(self) -> tuple:
        """本型号声明支持的连接方式（可多值）"""
        return self._supported

    @property
    def interface_kind(self) -> str:
        """实际链路类型：`LAN` / `SERIAL`；仿真且未指定端点时为空串"""
        return self._endpoint.kind if self._endpoint is not None else ""

    @property
    def backend(self) -> str:
        """实际生效的传输后端：`native`（标准库 socket / pyserial）或 `visa`（PyVISA）"""
        return self._backend

    @property
    def backends(self) -> tuple:
        """本型号声明支持的后端（可多值）"""
        return self._backends

    @property
    def visa_resource(self) -> str:
        """规范 VISA 资源名（`backend="visa"` 时真正使用）"""
        return self._endpoint.visa_resource if self._endpoint is not None else ""

    def backend_detail(self) -> dict:
        """后端详情：请求值 / 实际值 / 环境可用性（排障用，解释"为什么用了原生栈"）"""
        from .transport import visa_available

        return {"requested": self._backend_requested, "used": self._backend,
                "declared": list(self._backends),
                "visa_available": bool(visa_available()),
                "resource": self.visa_resource}

    @property
    def mode(self) -> str:
        """`real`（真机：网口 / 串口）或 `simulate`（仿真）"""
        return self._mode

    @property
    def simulated(self) -> bool:
        return self._mode != "real"

    def metadata(self) -> dict:
        """驱动与契约版本信息（排障/审计用）"""
        return {
            "kind": self.kind, "model": self._model, "vendor": self._entry["vendor"],
            "label": self._entry["label"], "driver": self._entry["driver"],
            "driver_version": self._entry["version"], "contract_api": self._entry["api"],
            "interfaces": list(self._supported), "interface": self.interface,
            "interface_kind": self.interface_kind, "mode": self._mode,
            "endpoint": self.endpoint, "endpoint_detail": self.endpoint_detail,
            "backend": self._backend, "backend_requested": self._backend_requested,
            "backends": list(self._backends), "visa_resource": self.visa_resource,
            "verified": self._verified,
        }

    @property
    def endpoint(self) -> str:
        """端点：网口 `192.168.10.41:4000` / 串口 `COM6@115200,8N`；仿真时为空"""
        return self._endpoint.label if self._endpoint is not None else ""

    @property
    def endpoint_detail(self) -> dict:
        """端点明细（连接方式 / 主机端口 或 串口参数）"""
        return self._endpoint.describe() if self._endpoint is not None else {}

    def supported_measurements(self) -> tuple:
        """本型号可测的项（统一 kind，跨厂商一致）"""
        return tuple(MEASURE_ITEMS)

    # ---------------------------------------------------------------- 会话
    def connect(self, verify_model: Optional[bool] = None) -> dict:
        """建立会话（网口 / 串口一视同仁）；校验开关打开时会核对设备回读的型号"""
        self._driver.open()
        if verify_model is not None:
            self._verify_model = bool(verify_model)
        if self._verify_model:
            self._check_model()
        return self.status()

    def close(self) -> dict:
        """安全退出（会先补发必要的收尾动作，断开连接）"""
        return self._driver.close()

    def __enter__(self) -> "Scope":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover
        state = "connected" if getattr(self._driver, "connected", False) else "closed"
        return "<Scope %s %s %s>" % (self._model, self.endpoint or "simulate", state)

    # ---------------------------------------------------------------- 业务动作
    def identity(self) -> dict:
        """设备身份：厂商 / 型号 / 序列号 / 固件（已解析，不是原始字符串）"""
        return self._parse_identity(self._driver.identify()["value"])

    def reset(self) -> dict:
        """恢复出厂设置并清状态"""
        return self._ok("reset", self._driver.reset())

    def auto_setup(self, freq_hz: Optional[float] = None, volts_pp: Optional[float] = None) -> dict:
        """一键自动设置；给了频率/幅值会据此挑档位"""
        res = self._driver.do("scope.autoset", freq_hz=freq_hz, vpp_v=volts_pp)
        return self._ok("auto_setup", res)

    def set_timebase(self, seconds_per_div: Optional[float] = None,
                     offset_seconds: Optional[float] = None,
                     divisions: Optional[int] = None) -> dict:
        """水平系统：每格时间 / 水平位移 / 总格数"""
        res = self._driver.do("scope.timebase", scale_s_per_div=seconds_per_div,
                              offset_s=offset_seconds, divisions=divisions)
        return self._ok("set_timebase", res)

    def set_channel(self, channel: str = "CH1", volts_per_div: Optional[float] = None,
                    coupling: Optional[str] = None, offset_volts: Optional[float] = None,
                    probe_ratio: Optional[float] = None, enabled: Optional[bool] = None) -> dict:
        """垂直系统：每格电压 / 耦合方式 / 垂直位移 / 探头比 / 通道开关"""
        res = self._driver.do("scope.channel", name=channel, scale_v_per_div=volts_per_div,
                              coupling=coupling, offset_v=offset_volts,
                              probe_ratio=probe_ratio, enabled=enabled)
        return self._ok("set_channel", res)

    def configure(self, channel: str = "CH1", seconds_per_div: Optional[float] = None,
                  volts_per_div: Optional[float] = None, coupling: Optional[str] = None,
                  offset_volts: Optional[float] = None, divisions: Optional[int] = None) -> dict:
        """一次配好水平 + 垂直（常用组合，省一次往返）"""
        out = {"timebase": self.set_timebase(seconds_per_div, divisions=divisions),
               "channel": self.set_channel(channel, volts_per_div=volts_per_div,
                                           coupling=coupling, offset_volts=offset_volts)}
        return {"ok": True, "simulated": self.simulated, "value": out}

    def run(self) -> dict:
        """连续采集"""
        return self._ok("run", self._driver.do("scope.run"))

    def stop(self) -> dict:
        """停止采集"""
        return self._ok("stop", self._driver.do("scope.stop"))

    def single(self) -> dict:
        """单次采集（等一次触发后停）"""
        return self._ok("single", self._driver.do("scope.single"))

    def capture(self, channel: str = "CH1", points: int = 1000) -> dict:
        """取一段波形：返回伏特序列 + 时间轴信息（单位恒为 V 与 s）"""
        res = self._driver.do("scope.acquire_waveform", points=int(points), channel=channel)
        val = res.get("value") or {}
        return {
            "ok": bool(res.get("ok", True)), "simulated": self.simulated,
            "channel": val.get("channel", channel),
            "points": int(val.get("points") or len(val.get("samples") or ())),
            "volts": tuple(val.get("samples") or ()),
            "unit": val.get("unit", "V"),
            "seconds_per_sample": val.get("x_incr_s"),
            "first_sample_seconds": val.get("x_start_s", 0.0),
            "source": val.get("source", self._mode),
        }

    def measure(self, items: Optional[Iterable[str]] = None, channel: str = "CH1",
                points: int = 2000) -> dict:
        """测量：返回 `{kind: {"value":…, "unit":…}}`，跨厂商 kind 与单位一致"""
        kinds = [self._normalize_kind(i) for i in (items if items else MEASURE_ITEMS)]
        res = self._driver.do("scope.measure", items=kinds, channel=channel, points=int(points))
        out = {}
        for row in (res.get("value") or ()):
            kind = self._normalize_kind(row.get("type"))
            out[kind] = {"value": row.get("value"), "unit": row.get("unit", ""),
                         "quality": row.get("quality", "good"), "source": row.get("source", self._mode)}
        return out

    def status(self) -> dict:
        """会话与运行状态（采集状态、水平/垂直设置、能力清单、最近错误）"""
        raw = self._driver.state()
        keep = ("connected", "running", "single", "resource", "idn", "timebase", "channels",
                "capabilities", "last_error", "opened_at")
        out = {k: raw[k] for k in keep if k in raw}
        out.update({"model": self._model, "vendor": self._entry["vendor"],
                    "driver": self._entry["driver"], "mode": self._mode,
                    "interfaces": list(self._supported), "interface": self.interface,
                    "interface_kind": self.interface_kind, "endpoint": self.endpoint,
                    "endpoint_detail": self.endpoint_detail, "simulated": self.simulated,
                    "backend": self._backend, "backends": list(self._backends),
                    "backend_detail": self.backend_detail(), "visa_resource": self.visa_resource})
        return out

    def self_test(self) -> dict:
        """连通与功能自检（派发后建议先跑一次；含连接方式检查）"""
        st = self.status()
        checks = [
            {"name": "会话已建立", "ok": bool(st.get("connected"))},
            {"name": "可用于采集", "ok": "scope.acquire_waveform" in (st.get("capabilities") or [])},
            {"name": "可测量", "ok": "scope.measure" in (st.get("capabilities") or [])},
            {"name": "型号已核对", "ok": self._verified is not False},
            {"name": "连接方式可用", "ok": bool(self._endpoint) or self.simulated},
            {"name": "传输后端可用",
             "ok": self._backend in self._backends
                   and (self._backend != VISA or bool(self.simulated) or visa_available())},
            {"name": "无遗留错误", "ok": not st.get("last_error")},
        ]
        return {"ok": all(c["ok"] for c in checks), "model": self._model, "vendor": self._entry["vendor"],
                "endpoint": self.endpoint, "simulated": self.simulated, "checks": checks,
                "backend": self._backend, "backends": list(self._backends),
                "visa_resource": self.visa_resource,
                "identity": self.identity() if st.get("connected") else {}}

    # ---------------------------------------------------------------- 内部
    def _resolve_mode(self, mode: str) -> str:
        m = str(mode or "auto").lower()
        if m == "auto":
            return "real" if self._endpoint is not None else "simulate"
        if m not in ("real", "simulate"):
            raise ConfigurationError("mode 只能是 real / simulate / auto", detail="收到 %r" % (mode,))
        return m

    def _check_model(self) -> None:
        """核对设备回读型号与登记型号（防"档案型号写错、连上了别的机器"）"""
        from . import factory

        info = self.identity()
        actual = info.get("model") or ""
        if factory.normalize_model(actual) == factory.normalize_model(self._model):
            self._verified = True
            return
        self._verified = False
        raise ConfigurationError(
            "设备回读型号与档案不符：档案 %s，实测 %s" % (self._model, actual or "(空)"),
            detail="请核对设备档案；确认是同一系列的近似型号时可改称 open_scope(..., verify_model=False)")

    @staticmethod
    def _parse_identity(idn: str) -> dict:
        parts = [p.strip() for p in str(idn or "").split(",")]
        while len(parts) < 4:
            parts.append("")
        return {"vendor": parts[0], "model": parts[1], "serial": parts[2], "firmware": parts[3],
                "idn": str(idn or "")}

    @staticmethod
    def _normalize_kind(kind: Any) -> str:
        if kind is None:
            return ""
        text = str(kind).strip()
        return MEASURE_ALIASES.get(text) or MEASURE_ALIASES.get(text.upper()) or text.upper()

    def _ok(self, action: str, res: dict) -> dict:
        return {"ok": bool(res.get("ok", True)), "action": action, "simulated": self.simulated,
                "value": res.get("value"), "source": res.get("source", self._mode)}


__all__ = ["KIND", "MEASURE_ALIASES", "Scope"]
