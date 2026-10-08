"""
wfb_ng.fl.radio: Flat radio parameter model, dynamic rate bounds, and 5GHz spectrum survey.

Per ADR-0014:
- 5GHz channels: strictly UNII-3 pool [149, 153, 157, 165], default 157.
- Channel 161 is permanently blacklisted (rtl88xxau driver PHY_GetTxPowerIndexBase crash).
- Radio TX power: independent parameter, range 10 ~ 20 dBm (desktop near-field recommended 12 dBm).
- Decoupled downlink MCS (3..6, default 3) and uplink MCS (3..6, recommended 6).
- Solidified FEC (k=8, n=14), bandwidth (HT40+), guard interval (Short GI, 400ns).
- Dynamic UFTP downlink injection rate bounds mapped to downlink_mcs.
"""

import argparse
import json
import os
import select
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

# Allowed 5GHz UNII-3 channel pool
ALLOWED_5GHZ_CHANNELS: Tuple[int, ...] = (149, 153, 157, 165)

# Hardcoded driver crash blacklist: rtl88xxau_wfb PHY_GetTxPowerIndexBase crash
FORBIDDEN_CHANNELS: Tuple[int, ...] = (161,)

DEFAULT_CHANNEL: int = 157

TXPOWER_MIN_DBM: int = 10
TXPOWER_MAX_DBM: int = 20
DEFAULT_TXPOWER_DBM: int = 12

MCS_MIN: int = 3
MCS_MAX: int = 6
ALLOWED_MCS_VALUES: Tuple[int, ...] = (3, 4, 5, 6)
DEFAULT_DOWNLINK_MCS: int = 3
RECOMMENDED_UPLINK_MCS: int = 6

FIXED_FEC_K: int = 8
FIXED_FEC_N: int = 14
FIXED_BANDWIDTH: str = "HT40+"
FIXED_GUARD_INTERVAL: str = "short"


@dataclass(frozen=True)
class RateBounds:
    downlink_mcs: int
    min_rate_kbps: int
    max_rate_kbps: int
    default_rate_kbps: int

    @property
    def min_rate_mbps(self) -> float:
        return self.min_rate_kbps / 1000.0

    @property
    def max_rate_mbps(self) -> float:
        return self.max_rate_kbps / 1000.0

    @property
    def default_rate_mbps(self) -> float:
        return self.default_rate_kbps / 1000.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "downlink_mcs": self.downlink_mcs,
            "min_rate_kbps": self.min_rate_kbps,
            "max_rate_kbps": self.max_rate_kbps,
            "default_rate_kbps": self.default_rate_kbps,
            "min_rate_mbps": self.min_rate_mbps,
            "max_rate_mbps": self.max_rate_mbps,
            "default_rate_mbps": self.default_rate_mbps,
        }


# Dynamic rate bounds table per ADR-0014
_DOWNLINK_RATE_BOUNDS: Dict[int, RateBounds] = {
    3: RateBounds(downlink_mcs=3, min_rate_kbps=12000, max_rate_kbps=18000, default_rate_kbps=15000),
    4: RateBounds(downlink_mcs=4, min_rate_kbps=18000, max_rate_kbps=26000, default_rate_kbps=22000),
    5: RateBounds(downlink_mcs=5, min_rate_kbps=24000, max_rate_kbps=35000, default_rate_kbps=28000),
    6: RateBounds(downlink_mcs=6, min_rate_kbps=32000, max_rate_kbps=45000, default_rate_kbps=38000),
}

__all__ = (
    "ALLOWED_5GHZ_CHANNELS",
    "FORBIDDEN_CHANNELS",
    "DEFAULT_CHANNEL",
    "TXPOWER_MIN_DBM",
    "TXPOWER_MAX_DBM",
    "DEFAULT_TXPOWER_DBM",
    "MCS_MIN",
    "MCS_MAX",
    "ALLOWED_MCS_VALUES",
    "DEFAULT_DOWNLINK_MCS",
    "RECOMMENDED_UPLINK_MCS",
    "FIXED_FEC_K",
    "FIXED_FEC_N",
    "FIXED_BANDWIDTH",
    "FIXED_GUARD_INTERVAL",
    "RateBounds",
    "get_downlink_rate_bounds",
    "RadioConfig",
    "validate_radio_config",
    "validate_radio_patch",
    "ChannelSurveyResult",
    "SpectrumSurveyReport",
    "SurveyBackend",
    "MockSurveyBackend",
    "LiveRadioSurveyBackend",
    "find_wlx_interfaces",
    "survey_spectrum",
    "main",
)


def get_downlink_rate_bounds(downlink_mcs: int) -> RateBounds:
    """
    Return dynamic UFTP downlink injection rate bounds and recommended default for a given downlink MCS.
    Raises ValueError if downlink_mcs is not in ALLOWED_MCS_VALUES (3..6).
    """
    if downlink_mcs not in _DOWNLINK_RATE_BOUNDS:
        raise ValueError(
            f"Invalid downlink_mcs={downlink_mcs!r}. Must be one of {ALLOWED_MCS_VALUES}."
        )
    return _DOWNLINK_RATE_BOUNDS[downlink_mcs]


@dataclass(frozen=True)
class RadioConfig:
    channel: int = DEFAULT_CHANNEL
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    downlink_mcs: int = DEFAULT_DOWNLINK_MCS
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    uftp_rate_kbps: int = field(default=15000)

    @property
    def fec_k(self) -> int:
        return FIXED_FEC_K

    @property
    def fec_n(self) -> int:
        return FIXED_FEC_N

    @property
    def channel_width(self) -> str:
        return FIXED_BANDWIDTH

    @property
    def guard_interval(self) -> str:
        return FIXED_GUARD_INTERVAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "radio_txpower_dbm": self.radio_txpower_dbm,
            "downlink_mcs": self.downlink_mcs,
            "uplink_mcs": self.uplink_mcs,
            "uftp_rate_kbps": self.uftp_rate_kbps,
            "fec_k": self.fec_k,
            "fec_n": self.fec_n,
            "channel_width": self.channel_width,
            "guard_interval": self.guard_interval,
        }


def validate_radio_config(config: Dict[str, Any]) -> RadioConfig:
    """
    Validate and construct a RadioConfig instance from dictionary config.
    Enforces ADR-0014 flat radio parameter rules:
    - channel strictly in ALLOWED_5GHZ_CHANNELS (149, 153, 157, 165).
    - Channel 161 is strictly forbidden with an explicit error.
    - radio_txpower_dbm in [10, 20].
    - downlink_mcs in [3, 4, 5, 6].
    - uplink_mcs in [3, 4, 5, 6].
    - uftp_rate_kbps in [min_rate_kbps, max_rate_kbps] for downlink_mcs.
    """
    if not isinstance(config, dict):
        raise TypeError(f"Config must be a dict, got {type(config).__name__}")

    raw_channel = config.get("channel", DEFAULT_CHANNEL)
    if not isinstance(raw_channel, int) or isinstance(raw_channel, bool):
        raise ValueError(f"channel must be an integer, got {raw_channel!r}")

    if raw_channel in FORBIDDEN_CHANNELS:
        raise ValueError(
            f"Channel {raw_channel} is strictly forbidden due to known driver kernel crash "
            "(rtl88xxau_wfb PHY_GetTxPowerIndexBase out-of-bounds defect)."
        )

    if raw_channel not in ALLOWED_5GHZ_CHANNELS:
        raise ValueError(
            f"Invalid channel {raw_channel}. Must be in legal 5 GHz UNII-3 pool {ALLOWED_5GHZ_CHANNELS}."
        )

    raw_txpower = config.get("radio_txpower_dbm", DEFAULT_TXPOWER_DBM)
    if not isinstance(raw_txpower, int) or isinstance(raw_txpower, bool):
        raise ValueError(f"radio_txpower_dbm must be an integer, got {raw_txpower!r}")
    if not (TXPOWER_MIN_DBM <= raw_txpower <= TXPOWER_MAX_DBM):
        raise ValueError(
            f"radio_txpower_dbm={raw_txpower} out of valid range [{TXPOWER_MIN_DBM}, {TXPOWER_MAX_DBM}] dBm."
        )

    raw_downlink_mcs = config.get("downlink_mcs", DEFAULT_DOWNLINK_MCS)
    if not isinstance(raw_downlink_mcs, int) or isinstance(raw_downlink_mcs, bool):
        raise ValueError(f"downlink_mcs must be an integer, got {raw_downlink_mcs!r}")
    if raw_downlink_mcs not in ALLOWED_MCS_VALUES:
        raise ValueError(
            f"downlink_mcs={raw_downlink_mcs} invalid. Must be one of {ALLOWED_MCS_VALUES}."
        )

    raw_uplink_mcs = config.get("uplink_mcs", RECOMMENDED_UPLINK_MCS)
    if not isinstance(raw_uplink_mcs, int) or isinstance(raw_uplink_mcs, bool):
        raise ValueError(f"uplink_mcs must be an integer, got {raw_uplink_mcs!r}")
    if raw_uplink_mcs not in ALLOWED_MCS_VALUES:
        raise ValueError(
            f"uplink_mcs={raw_uplink_mcs} invalid. Must be one of {ALLOWED_MCS_VALUES}."
        )

    bounds = get_downlink_rate_bounds(raw_downlink_mcs)
    raw_rate = config.get("uftp_rate_kbps")
    if raw_rate is None:
        rate = bounds.default_rate_kbps
    else:
        if not isinstance(raw_rate, int) or isinstance(raw_rate, bool):
            raise ValueError(f"uftp_rate_kbps must be an integer, got {raw_rate!r}")
        if not (bounds.min_rate_kbps <= raw_rate <= bounds.max_rate_kbps):
            raise ValueError(
                f"uftp_rate_kbps={raw_rate} outside safe bounds for downlink_mcs={raw_downlink_mcs} "
                f"[{bounds.min_rate_kbps}, {bounds.max_rate_kbps}] Kbps "
                f"({bounds.min_rate_mbps}~{bounds.max_rate_mbps} Mbps)."
            )
        rate = raw_rate

    return RadioConfig(
        channel=raw_channel,
        radio_txpower_dbm=raw_txpower,
        downlink_mcs=raw_downlink_mcs,
        uplink_mcs=raw_uplink_mcs,
        uftp_rate_kbps=rate,
    )


def validate_radio_patch(
    patch: Dict[str, Any], base: Optional[RadioConfig] = None
) -> RadioConfig:
    """
    Validate and apply an incremental radio reconfiguration patch onto a base RadioConfig.
    If base is not provided, defaults to default RadioConfig().
    """
    if base is None:
        base = RadioConfig()
    merged = base.to_dict()

    # If downlink_mcs is being changed but uftp_rate_kbps is not explicitly specified in patch,
    # auto-adapt the default rate for the new MCS.
    if "downlink_mcs" in patch and "uftp_rate_kbps" not in patch:
        new_mcs = patch["downlink_mcs"]
        if isinstance(new_mcs, int) and new_mcs in ALLOWED_MCS_VALUES:
            merged["uftp_rate_kbps"] = get_downlink_rate_bounds(new_mcs).default_rate_kbps

    merged.update(patch)
    return validate_radio_config(merged)


@dataclass(frozen=True)
class ChannelSurveyResult:
    channel: int
    frame_count: int
    duration_ms: float
    fps: float
    rank: int
    is_congested: bool
    density_level: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "frame_count": self.frame_count,
            "duration_ms": self.duration_ms,
            "fps": round(self.fps, 2),
            "rank": self.rank,
            "is_congested": self.is_congested,
            "density_level": self.density_level,
        }


@dataclass(frozen=True)
class SpectrumSurveyReport:
    interface: Optional[str]
    results: List[ChannelSurveyResult]
    recommended_channel: int
    all_congested: bool
    risk_warning: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface": self.interface,
            "results": [r.to_dict() for r in self.results],
            "recommended_channel": self.recommended_channel,
            "all_congested": self.all_congested,
            "risk_warning": self.risk_warning,
        }


class SurveyBackend(Protocol):
    def count_frames(self, interface: str, channel: int, duration_ms: float) -> int:
        ...

    def prepare(self, interface: str) -> None:
        ...

    def finish(self, interface: str) -> None:
        ...


class MockSurveyBackend:
    """Mock backend for deterministic testing and dry-run environments."""

    def __init__(self, channel_frames: Optional[Dict[int, int]] = None):
        self.channel_frames: Dict[int, int] = dict(channel_frames or {})
        self.queried_channels: List[int] = []
        self.prepared: bool = False
        self.finished: bool = False

    def prepare(self, interface: str) -> None:
        self.prepared = True

    def count_frames(self, interface: str, channel: int, duration_ms: float) -> int:
        if channel in FORBIDDEN_CHANNELS:
            raise ValueError(f"Channel {channel} is forbidden from survey query")
        self.queried_channels.append(channel)
        return self.channel_frames.get(channel, 0)

    def finish(self, interface: str) -> None:
        self.finished = True


def find_wlx_interfaces() -> List[str]:
    """Find all wireless interfaces whose names begin with 'wlx'."""
    interfaces: List[str] = []
    # Try sysfs first
    sysfs_net = "/sys/class/net"
    if os.path.isdir(sysfs_net):
        try:
            for name in os.listdir(sysfs_net):
                if name.startswith("wlx"):
                    interfaces.append(name)
        except OSError:
            pass
    if not interfaces:
        # Fallback to iw dev
        try:
            out = subprocess.check_output(["iw", "dev"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Interface "):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith("wlx"):
                        interfaces.append(parts[1])
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(list(set(interfaces)))


class LiveRadioSurveyBackend:
    """Live hardware survey backend using Linux netlink/iw and AF_PACKET raw socket."""

    def __init__(self, use_sudo: bool = True):
        self.use_sudo = use_sudo
        self.initial_channel: Optional[int] = None

    def prepare(self, interface: str) -> None:
        self.initial_channel = self._get_current_channel(interface)

    def _get_current_channel(self, interface: str) -> Optional[int]:
        try:
            out = subprocess.check_output(["iw", "dev", interface, "info"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("channel "):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _set_channel(self, interface: str, channel: int) -> None:
        cmd = ["iw", "dev", interface, "set", "channel", str(channel)]
        if self.use_sudo and os.geteuid() != 0:
            cmd = ["sudo"] + cmd
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to set interface {interface} to channel {channel}: {res.stderr.strip()}"
            )

    def count_frames(self, interface: str, channel: int, duration_ms: float) -> int:
        if channel in FORBIDDEN_CHANNELS:
            raise ValueError(f"Channel {channel} is forbidden from survey query")

        self._set_channel(interface, channel)

        # Allow hardware to settle on frequency
        time.sleep(0.05)

        ETH_P_ALL = 0x0003
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        except PermissionError as exc:
            raise PermissionError(
                f"Raw socket capture requires root privileges (CAP_NET_RAW). "
                f"Please run survey with sudo. ({exc})"
            ) from exc

        count = 0
        try:
            sock.bind((interface, 0))
            sock.setblocking(False)
            deadline = time.time() + (duration_ms / 1000.0)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                r, _, _ = select.select([sock], [], [], min(remaining, 0.05))
                if r:
                    try:
                        while True:
                            sock.recv(4096)
                            count += 1
                    except (BlockingIOError, InterruptedError):
                        pass
        finally:
            sock.close()

        return count

    def finish(self, interface: str) -> None:
        # Restore original channel to prevent unintended background channel hopping
        if self.initial_channel and self.initial_channel in ALLOWED_5GHZ_CHANNELS:
            try:
                self._set_channel(interface, self.initial_channel)
            except Exception:
                pass


def _classify_density(fps: float) -> str:
    if fps <= 0.0:
        return "clean"
    elif fps < 10.0:
        return "low"
    elif fps < 50.0:
        return "moderate"
    elif fps < 100.0:
        return "congested"
    else:
        return "busy"


def survey_spectrum(
    interface: Optional[str] = None,
    duration_ms: float = 300.0,
    backend: Optional[SurveyBackend] = None,
    congestion_threshold_fps: float = 50.0,
) -> SpectrumSurveyReport:
    """
    Survey legal 5 GHz UNII-3 channels [149, 153, 157, 165].
    Permanently excludes Channel 161.
    Ranks channels by frame density (cleanest first).
    Issues risk warning if all channels are congested.
    """
    if backend is None:
        if not interface:
            detected = find_wlx_interfaces()
            if not detected:
                raise RuntimeError(
                    "No wlx* wireless interface detected. Specify --interface or connect wireless card."
                )
            interface = detected[0]
        backend = LiveRadioSurveyBackend()
    else:
        if not interface:
            interface = "mock"

    results_raw: List[Dict[str, Any]] = []
    if hasattr(backend, "prepare"):
        backend.prepare(interface)

    try:
        for ch in ALLOWED_5GHZ_CHANNELS:
            frames = backend.count_frames(interface=interface, channel=ch, duration_ms=duration_ms)
            duration_sec = max(duration_ms / 1000.0, 0.001)
            fps = frames / duration_sec
            is_congested = fps >= congestion_threshold_fps
            density_level = _classify_density(fps)
            results_raw.append({
                "channel": ch,
                "frame_count": frames,
                "duration_ms": duration_ms,
                "fps": fps,
                "is_congested": is_congested,
                "density_level": density_level,
            })
    finally:
        if hasattr(backend, "finish"):
            backend.finish(interface)

    # Sort results: cleanest first (lowest fps), tie-break with channel number
    results_raw.sort(key=lambda x: (x["fps"], x["channel"]))

    ranked_results: List[ChannelSurveyResult] = []
    for idx, r in enumerate(results_raw, start=1):
        ranked_results.append(
            ChannelSurveyResult(
                channel=r["channel"],
                frame_count=r["frame_count"],
                duration_ms=r["duration_ms"],
                fps=r["fps"],
                rank=idx,
                is_congested=r["is_congested"],
                density_level=r["density_level"],
            )
        )

    all_congested = all(r.is_congested for r in ranked_results)
    risk_warning: Optional[str] = None
    if all_congested:
        risk_warning = (
            f"警告：全频段（{', '.join(str(c) for c in ALLOWED_5GHZ_CHANNELS)}）检测到较强外部 802.11 "
            f"帧密度干扰（帧率均 >= {congestion_threshold_fps} fps），信道拥堵风险较高，请注意空口质量！"
        )

    recommended_channel = ranked_results[0].channel

    return SpectrumSurveyReport(
        interface=interface,
        results=ranked_results,
        recommended_channel=recommended_channel,
        all_congested=all_congested,
        risk_warning=risk_warning,
    )


def _cmd_rates(args: argparse.Namespace) -> int:
    try:
        if args.downlink_mcs is not None:
            bounds = get_downlink_rate_bounds(args.downlink_mcs)
            if args.json:
                print(json.dumps(bounds.as_dict(), indent=2))
            else:
                print(f"Downlink MCS {bounds.downlink_mcs}:")
                print(f"  UFTP Rate: {bounds.min_rate_kbps} ~ {bounds.max_rate_kbps} Kbps ({bounds.min_rate_mbps} ~ {bounds.max_rate_mbps} Mbps)")
                print(f"  Recommended default: {bounds.default_rate_kbps} Kbps ({bounds.default_rate_mbps} Mbps)")
        else:
            all_bounds = {mcs: get_downlink_rate_bounds(mcs).as_dict() for mcs in ALLOWED_MCS_VALUES}
            if args.json:
                print(json.dumps(all_bounds, indent=2))
            else:
                print("==========================================================================")
                print("下行组播 (UFTP) 调制与动态注入速率约束表 (ADR-0014 固化 FEC 8/14 HT40+)")
                print("==========================================================================")
                print(f"{'MCS':<6} {'推荐默认速率':<24} {'允许安全区间':<26}")
                print("--------------------------------------------------------------------------")
                for mcs in ALLOWED_MCS_VALUES:
                    b = get_downlink_rate_bounds(mcs)
                    default_str = f"{b.default_rate_kbps} Kbps ({b.default_rate_mbps} Mbps)"
                    range_str = f"{b.min_rate_kbps} ~ {b.max_rate_kbps} Kbps ({b.min_rate_mbps} ~ {b.max_rate_mbps} Mbps)"
                    print(f"MCS {mcs:<2} {default_str:<24} {range_str:<26}")
                print("--------------------------------------------------------------------------")
                print("提示：上行 HTTP PUT (TCP) 不受限，全速饱和传输（推荐 MCS 6）。")
        return 0
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def _cmd_validate(args: argparse.Namespace) -> int:
    raw_cfg: Dict[str, Any] = {}
    if args.channel is not None:
        raw_cfg["channel"] = args.channel
    if args.txpower is not None:
        raw_cfg["radio_txpower_dbm"] = args.txpower
    if args.downlink_mcs is not None:
        raw_cfg["downlink_mcs"] = args.downlink_mcs
    if args.uplink_mcs is not None:
        raw_cfg["uplink_mcs"] = args.uplink_mcs
    if args.uftp_rate is not None:
        raw_cfg["uftp_rate_kbps"] = args.uftp_rate

    try:
        validated = validate_radio_config(raw_cfg)
        if args.json:
            print(json.dumps(validated.to_dict(), indent=2))
        else:
            print("==========================================================")
            print("扁平射频参数模型校验通过 (ADR-0014)")
            print("==========================================================")
            d = validated.to_dict()
            for k, v in d.items():
                print(f"  {k:<20}: {v}")
        return 0
    except (ValueError, TypeError) as exc:
        print(f"射频参数校验失败: {exc}", file=sys.stderr)
        return 1


def _cmd_survey(args: argparse.Namespace) -> int:
    backend: Optional[SurveyBackend] = None
    if args.mock_frames:
        mapping: Dict[int, int] = {}
        for item in args.mock_frames.split(","):
            if ":" in item:
                ch_str, count_str = item.split(":", 1)
                mapping[int(ch_str.strip())] = int(count_str.strip())
        backend = MockSurveyBackend(mapping)

    try:
        report = survey_spectrum(
            interface=args.interface,
            duration_ms=args.duration_ms,
            backend=backend,
            congestion_threshold_fps=args.congestion_threshold,
        )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print("==========================================================================")
            print(f"5 GHz 射频扫频探路结果 (接口: {report.interface}, 单信道采样: {args.duration_ms}ms)")
            print("==========================================================================")
            print(f"{'排名':<6} {'信道':<6} {'帧数':<8} {'帧率 (fps)':<14} {'状态':<12} {'建议':<12}")
            print("--------------------------------------------------------------------------")
            for r in report.results:
                star = "★ 推荐信道" if r.channel == report.recommended_channel else ""
                print(f"#{r.rank:<5} {r.channel:<6} {r.frame_count:<8} {r.fps:<14.1f} {r.density_level:<12} {star}")
            print("--------------------------------------------------------------------------")
            print(f"推荐信道: Channel {report.recommended_channel}")
            if report.risk_warning:
                print()
                print(f"{report.risk_warning}")
        return 0
    except Exception as exc:
        print(f"扫频执行失败: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wfb-fl-radio",
        description="WFB-FL 5GHz 射频感知、参数直配模型与速率约束生成工具 (ADR-0014)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令")

    # Subcommand: rates
    p_rates = subparsers.add_parser("rates", help="查询下行组播 UFTP 动态注入速率安全区间与推荐值")
    p_rates.add_argument("-d", "--downlink-mcs", type=int, choices=ALLOWED_MCS_VALUES, help="下行调制阶数 (3..6)")
    p_rates.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # Subcommand: validate
    p_val = subparsers.add_parser("validate", help="校验扁平射频参数模型")
    p_val.add_argument("-c", "--channel", type=int, help=f"5 GHz 信道 {ALLOWED_5GHZ_CHANNELS}")
    p_val.add_argument("-p", "--txpower", type=int, help="发射功率 dBm (10..20, 近距推荐 12)")
    p_val.add_argument("-d", "--downlink-mcs", type=int, help="下行调制阶数 (3..6, 默认 3)")
    p_val.add_argument("-u", "--uplink-mcs", type=int, help="上行调制阶数 (3..6, 推荐 6)")
    p_val.add_argument("-r", "--uftp-rate", type=int, help="下行 UFTP 注入速率 Kbps")
    p_val.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # Subcommand: survey
    p_surv = subparsers.add_parser("survey", help="执行 5 GHz 频段快速扫频探路并输出干净度排行")
    p_surv.add_argument("-i", "--interface", type=str, help="无线网卡接口名称 (默认自动探测 wlx*)")
    p_surv.add_argument("-d", "--duration-ms", type=float, default=300.0, help="单信道嗅探时长毫秒 (默认 300ms)")
    p_surv.add_argument("-t", "--congestion-threshold", type=float, default=50.0, help="拥堵判定阈值 fps (默认 50)")
    p_surv.add_argument("--mock-frames", type=str, help="模拟数据 'ch:frames,ch:frames' (用于无网卡测试)")
    p_surv.add_argument("--json", action="store_true", help="输出 JSON 格式")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "rates":
        return _cmd_rates(args)
    elif args.subcommand == "validate":
        return _cmd_validate(args)
    elif args.subcommand == "survey":
        return _cmd_survey(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())



