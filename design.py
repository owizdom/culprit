"""The chip CULPRIT is pointed at: every design-specific setting the product code uses, in one place.

DEFAULTS is PicoRV32 (owizdom/picorv32-ci). A `design:` section in config.yaml or config.local.yaml is merged on top
(dictionaries merge key by key, lists are replaced), so another design with a self-checking Icarus testbench is a
config change. `firmware: null` means the testbench needs no firmware image; `log.end: null` means the pass marker is
searched for in the whole log; a null entry under `yosys.chparams` or `memories` removes that entry.
"""
import copy
import hashlib
import json

from apps import http

DEFAULTS = {
    "name": "PicoRV32",
    "description": "a RISC-V CPU written in Verilog",
    "rtl_file": "picorv32.v",                       # the one RTL file CULPRIT localizes in, elaborates and repairs
    "sources": ["testbench.v", "picorv32.v"],       # compiled by iverilog in this order
    "tree_suffixes": [".v", ".S", ".h", ".c", ".lds", ".py", ""],
    "sim": {"top": "testbench", "defines": ["COMPRESSED_ISA"], "timeout_s": 1800},
    "firmware": {
        "inputs": ["Makefile", "firmware", "tests"],  # top-level files and folders the image is built from
        "image": "culprit-tc",
        "dockerfile_dir": "sim",
        "command": ["make", "firmware/firmware.hex"],
        "output": "firmware/firmware.hex",
        "sim_name": "firmware.hex",                   # testbench.v keeps +firmware in 128 characters: keep it short
        "plusarg": "+firmware=firmware.hex",
        "seed_dir": "sim/fw-seed",
        "timeout_s": 600,
    },
    "log": {
        "end": r"TRAP after (\d+) clock cycles",
        "pass": r"^ALL TESTS PASSED\.$",
        "timeout": "TIMEOUT",
        "test_line": r"^(\w+)\.\.(OK|ERROR)$",
        "test_fail": "ERROR",
        "named_tests": ["sieve", "multest"],
        "reason": r"(EBREAK instruction|Illegal Instruction|Bus error in Instruction) at 0x([0-9a-fA-F]+)"
                  r"|(OUT-OF-BOUNDS MEMORY (?:READ FROM|WRITE TO)) (\S+)",
        "end_time": r"\$(?:finish|stop) called at (\d+) \(1ps\)",
    },
    "timing": {"reset_cycles": 100, "period_ns": 10},  # testbench.v holds resetn low for 100 edges; clk period 10 ns
    "yosys": {"top": "picorv32_axi",
              "chparams": {"COMPRESSED_ISA": 1, "ENABLE_MUL": 1, "ENABLE_DIV": 1, "ENABLE_IRQ": 1, "ENABLE_TRACE": 1}},
    "hierarchy": {"sim_core": "testbench.top.uut.picorv32_core", "netlist_prefix": "picorv32_core."},
    "observed_ports": ["mem_valid", "mem_instr", "mem_addr", "mem_wdata", "mem_wstrb", "trace_valid", "trace_data",
                       "trap"],
    "memories": {"cpuregs": 32},                      # memory name below the core -> words dumped one by one
    "corpus_features": True,                          # Test Run and the mutant kill rate use the PicoRV32 corpus
}

_cache = {"key": None, "value": None, "digest": ""}


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _build():
    value = http.merged(copy.deepcopy(DEFAULTS), http.config().get("design") or {})
    for section in ("chparams",):
        if isinstance(value.get("yosys"), dict) and isinstance(value["yosys"].get(section), dict):
            value["yosys"][section] = {k: v for k, v in value["yosys"][section].items() if v is not None}
    if isinstance(value.get("memories"), dict):
        value["memories"] = {k: v for k, v in value["memories"].items() if v is not None}
    same = value == DEFAULTS
    return value, "" if same else hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _current():
    # Re-read only when a config file changed. A replaced http.config (tests) is never cached.
    if getattr(http.config, "__module__", None) != http.__name__:
        value, dig = _build()
        return value, dig
    key = (str(http.CONFIG), _mtime(http.CONFIG), str(http.LOCAL_CONFIG), _mtime(http.LOCAL_CONFIG))
    if _cache["key"] != key:
        value, dig = _build()
        _cache.update(key=key, value=value, digest=dig)
    return _cache["value"], _cache["digest"]


def get():
    """The effective design: DEFAULTS with the config's `design:` section merged on top. Do not mutate it."""
    return _current()[0]


def digest():
    """'' for the default design (so existing simulation caches stay valid), else a short hash of the design."""
    return _current()[1]


def defines(cfg=None):
    """iverilog -D flags."""
    cfg = cfg or get()
    return [f"-D{d}" for d in (cfg["sim"].get("defines") or [])]


def chparams(cfg=None):
    """'-chparam K V ...' in the configured order."""
    cfg = cfg or get()
    params = (cfg.get("yosys") or {}).get("chparams") or {}
    return " ".join(f"-chparam {k} {int(v) if isinstance(v, bool) else v}" for k, v in params.items())


def hierarchy(cfg=None):
    """The Yosys hierarchy command, e.g. 'hierarchy -top picorv32_axi -chparam COMPRESSED_ISA 1 ...'."""
    cfg = cfg or get()
    top = (cfg.get("yosys") or {}).get("top")
    return " ".join(["hierarchy", f"-top {top}" if top else "-auto-top", chparams(cfg)]).rstrip()


def core_prefix(cfg=None):
    """What precedes a core signal's name in a simulator trace path ('picorv32_core.')."""
    cfg = cfg or get()
    h = cfg["hierarchy"]
    return h.get("netlist_prefix") or (h["sim_core"].split(".")[-1] + ".")
