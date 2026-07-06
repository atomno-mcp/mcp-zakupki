"""CLI-тесты точки входа `atomno-mcp-zakupki`.

Цель — зафиксировать, что `--help`, `--version` и валидация
`--transport` / `--log-level` работают БЕЗ запуска MCP-сервера
(иначе stdio-цикл повис бы в pytest).

Структура повторяет test_cli.py эталонного `mcp-fns-check-client`
(см. PRODUCTS/ATOMNO/_knowledge/MCP_BUILD_CHECKLIST.md §6).
"""

from __future__ import annotations

import pytest

from mcp_zakupki import __version__
from mcp_zakupki.server import (
    _DEFAULT_HTTP_HOST,
    _DEFAULT_HTTP_PORT,
    _SUPPORTED_TRANSPORTS,
    _build_arg_parser,
    _resolve_log_level,
    main,
)


class TestHelp:
    def test_dash_dash_help_prints_usage_and_exits_zero(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-zakupki" in out
        assert "--transport" in out
        assert "--version" in out
        assert "--log-level" in out

    def test_dash_h_short_flag_also_works(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["-h"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-zakupki" in out


class TestVersion:
    def test_dash_dash_version_prints_version_and_exits_zero(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out
        assert "atomno-mcp-zakupki" in out

    def test_dash_big_v_short_flag_also_works(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["-V"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out


class TestTransportValidation:
    def test_invalid_transport_exits_two_without_starting_server(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--transport", "nonexistent-transport"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "invalid choice" in err.lower() or "nonexistent-transport" in err

    def test_all_documented_transports_pass_validation(self) -> None:
        parser = _build_arg_parser()
        for t in _SUPPORTED_TRANSPORTS:
            args = parser.parse_args(["--transport", t])
            assert args.transport == t

    def test_default_transport_is_stdio(self) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.transport == "stdio"


class TestLogLevelValidation:
    def test_invalid_log_level_exits_two(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--log-level", "TRACE"])
        assert exc_info.value.code == 2

    def test_all_valid_log_levels_pass(self) -> None:
        parser = _build_arg_parser()
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            args = parser.parse_args(["--log-level", lvl])
            assert args.log_level == lvl

    def test_resolve_cli_flag_wins_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_LOG_LEVEL", "ERROR")
        assert _resolve_log_level("DEBUG") == "DEBUG"

    def test_resolve_falls_back_to_env_when_cli_none(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_LOG_LEVEL", "WARNING")
        assert _resolve_log_level(None) == "WARNING"

    def test_resolve_normalizes_env_case_and_whitespace(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_LOG_LEVEL", "  debug  ")
        assert _resolve_log_level(None) == "DEBUG"

    def test_resolve_default_is_info_when_nothing_set(self, monkeypatch) -> None:
        monkeypatch.delenv("MCP_ZAKUPKI_LOG_LEVEL", raising=False)
        assert _resolve_log_level(None) == "INFO"

    def test_resolve_rejects_invalid_env_loudly(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_LOG_LEVEL", "TRACE")
        with pytest.raises(ValueError, match="MCP_ZAKUPKI_LOG_LEVEL"):
            _resolve_log_level(None)


class TestParserDefaults:
    def test_default_host_is_localhost(self) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.host == _DEFAULT_HTTP_HOST == "127.0.0.1"

    def test_default_port_is_8000(self) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.port == _DEFAULT_HTTP_PORT == 8000

    def test_port_flag_parses_as_int(self) -> None:
        args = _build_arg_parser().parse_args(["--port", "9090"])
        assert args.port == 9090
        assert isinstance(args.port, int)


class TestInvalidEnvBailsOutCleanly:
    """Регрессия: невалидный MCP_ZAKUPKI_LOG_LEVEL не должен тихо падать в INFO
    и тем более запускать mcp.run() — лучше exit-2 c явной диагностикой."""

    def test_invalid_env_and_no_cli_flag_exits_two(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_LOG_LEVEL", "VERBOSE")
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "MCP_ZAKUPKI_LOG_LEVEL" in err


class TestCheckConfig:
    def test_check_config_exits_zero_without_api_keys(self, capsys) -> None:
        code = main(["--check-config"])
        assert code == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-zakupki" in out
        assert "provider_chain" in out
        assert "WARN" in out

    def test_check_config_shows_ok_when_damia_key_set(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_DAMIA_KEY", "test-key")
        code = main(["--check-config"])
        assert code == 0
        out = capsys.readouterr().out
        assert "damia            configured" in out
        assert "OK:" in out
