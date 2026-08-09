"""TASK-02 config tests. Every case listed in docs/TASK-02-config-and-secrets.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from agent.config import Settings, get_settings, validate_credentials

SECRET = "sk-ant-SUPERSECRET"


# ── loading and validation ─────────────────────────────────────────────


def test_defaults_load() -> None:
    """Settings construct with nothing set."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "local"
    assert s.llm_model == "claude-opus-5"
    assert s.budget_total_ms == 800


def test_azure_region_defaults_to_southeastasia() -> None:
    """A US region silently adds ~200ms — most of the 110ms TTS budget."""
    assert Settings(_env_file=None).azure_speech_region == "southeastasia"  # type: ignore[call-arg]


def test_unknown_env_var_rejected() -> None:
    """extra='forbid' turns a typo into a startup crash, not a silent default."""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, tts_providr="azure")  # type: ignore[call-arg]
    assert "tts_providr" in str(exc.value)


def test_effort_high_rejected() -> None:
    """'high' and above cannot fit the 300ms TTFT budget."""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, llm_effort="high")  # type: ignore[arg-type,call-arg]
    assert "llm_effort" in str(exc.value)


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_effort_low_and_medium_accepted(effort: str) -> None:
    assert Settings(_env_file=None, llm_effort=effort).llm_effort == effort  # type: ignore[arg-type,call-arg]


def test_retrieval_top_k_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, retrieval_top_k=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, retrieval_top_k=21)  # type: ignore[call-arg]
    assert Settings(_env_file=None, retrieval_top_k=20).retrieval_top_k == 20  # type: ignore[call-arg]


def test_answer_cache_threshold_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, answer_cache_threshold=1.5)  # type: ignore[call-arg]


# ── credential validation ───────────────────────────────────────────────


def test_missing_credential_names_the_variable() -> None:
    """The error must name the exact env var, not just 'config invalid'."""
    s = Settings(_env_file=None, stt_provider="deepgram", tts_provider="fake", llm_provider="fake")  # type: ignore[call-arg]
    with pytest.raises(RuntimeError) as exc:
        validate_credentials(s)
    assert "DEEPGRAM_API_KEY" in str(exc.value)
    assert "stt_provider=deepgram" in str(exc.value)


def test_missing_credentials_reports_all_not_just_first() -> None:
    s = Settings(
        _env_file=None, stt_provider="deepgram", tts_provider="azure", llm_provider="claude"
    )  # type: ignore[call-arg]
    with pytest.raises(RuntimeError) as exc:
        validate_credentials(s)
    message = str(exc.value)
    assert "DEEPGRAM_API_KEY" in message
    assert "AZURE_SPEECH_KEY" in message
    assert "ANTHROPIC_API_KEY" in message


def test_fake_providers_need_no_credentials() -> None:
    """A developer working on normalisation must not need an Azure key."""
    s = Settings(_env_file=None, stt_provider="fake", tts_provider="fake", llm_provider="fake")  # type: ignore[call-arg]
    validate_credentials(s)  # must not raise


def test_unselected_provider_credentials_not_required() -> None:
    """Inworld key is irrelevant when TTS_PROVIDER=azure."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        stt_provider="fake",
        tts_provider="azure",
        llm_provider="fake",
        azure_speech_key=SecretStr("present"),
    )
    validate_credentials(s)  # must not raise despite empty inworld_api_key


def test_populated_credentials_pass(settings: Settings) -> None:
    validate_credentials(settings)


def test_openrouter_requires_its_own_key() -> None:
    s = Settings(
        _env_file=None, stt_provider="fake", tts_provider="fake", llm_provider="openrouter"
    )  # type: ignore[call-arg]
    with pytest.raises(RuntimeError) as exc:
        validate_credentials(s)
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_openrouter_does_not_require_anthropic_key() -> None:
    """Switching provider must not demand the credential you switched away from."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        stt_provider="fake",
        tts_provider="fake",
        llm_provider="openrouter",
        openrouter_api_key=SecretStr("sk-or-test"),
    )
    validate_credentials(s)  # must not raise


# ── model id resolution ───────────────────────────────────────────────────


def test_openrouter_model_gets_vendor_prefix() -> None:
    """OpenRouter namespaces models; Anthropic does not. Same LLM_MODEL, both work."""
    s = Settings(_env_file=None, llm_provider="openrouter", llm_model="claude-opus-5")  # type: ignore[call-arg]
    assert s.resolved_llm_model == "anthropic/claude-opus-5"


def test_direct_anthropic_model_is_unprefixed() -> None:
    s = Settings(_env_file=None, llm_provider="claude", llm_model="claude-opus-5")  # type: ignore[call-arg]
    assert s.resolved_llm_model == "claude-opus-5"


def test_already_namespaced_model_not_double_prefixed() -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="openrouter", llm_model="anthropic/claude-sonnet-5"
    )
    assert s.resolved_llm_model == "anthropic/claude-sonnet-5"


def test_non_anthropic_openrouter_model_preserved() -> None:
    """A deliberate comparator model must survive resolution untouched."""
    s = Settings(_env_file=None, llm_provider="openrouter", llm_model="openai/gpt-5-mini")  # type: ignore[call-arg]
    assert s.resolved_llm_model == "openai/gpt-5-mini"


def test_openrouter_fast_mode_swaps_model_id() -> None:
    """OpenRouter exposes fast mode as a separate model id, not a request param."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="openrouter", llm_model="claude-opus-5", llm_fast_mode=True
    )
    assert s.resolved_llm_model == "anthropic/claude-opus-5-fast"


def test_openrouter_fast_mode_ignored_without_variant() -> None:
    """No sonnet-fast exists — requesting one would 404. Fall back to the base id."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="openrouter", llm_model="claude-sonnet-5", llm_fast_mode=True
    )
    assert s.resolved_llm_model == "anthropic/claude-sonnet-5"


def test_direct_api_fast_mode_does_not_touch_model_id() -> None:
    """The Anthropic API takes speed='fast' as a parameter; TASK-03 handles that."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="claude", llm_model="claude-opus-5", llm_fast_mode=True
    )
    assert s.resolved_llm_model == "claude-opus-5"


# ── secret hygiene ───────────────────────────────────────────────────────


def test_secrets_not_in_repr() -> None:
    s = Settings(_env_file=None, anthropic_api_key=SecretStr(SECRET))  # type: ignore[call-arg]
    assert SECRET not in repr(s)
    assert SECRET not in str(s)


def test_secrets_not_in_model_dump() -> None:
    """model_dump() output is the thing most likely to reach a log aggregator."""
    s = Settings(_env_file=None, anthropic_api_key=SecretStr(SECRET))  # type: ignore[call-arg]
    assert SECRET not in str(s.model_dump())


def test_secrets_not_in_json_dump() -> None:
    s = Settings(_env_file=None, anthropic_api_key=SecretStr(SECRET))  # type: ignore[call-arg]
    assert SECRET not in s.model_dump_json()


def test_secret_still_readable_at_point_of_use() -> None:
    s = Settings(_env_file=None, anthropic_api_key=SecretStr(SECRET))  # type: ignore[call-arg]
    assert s.anthropic_api_key.get_secret_value() == SECRET


# ── documentation drift ───────────────────────────────────────────────────


def test_env_example_covers_all_fields(project_root: Path) -> None:
    """Drift here means the next developer hits an undocumented required var."""
    text = (project_root / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = set(Settings.model_fields) - documented
    assert not missing, f"undocumented settings: {sorted(missing)}"


def test_env_example_has_no_real_secrets(project_root: Path) -> None:
    """A committed credential is the single worst thing this repo could contain.

    The credential list is derived from the SecretStr fields on Settings rather
    than from substring matching, so it stays correct as fields are added and
    does not false-positive on names like LLM_MAX_TOKENS.
    """
    secret_fields = {
        name.lower()
        for name, field in Settings.model_fields.items()
        if field.annotation is SecretStr
    }
    assert secret_fields, "expected Settings to declare SecretStr credentials"

    text = (project_root / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip().lower() in secret_fields:
            assert value.strip() == "", f"{name.strip()} must be blank in .env.example"


def test_no_env_access_outside_config(project_root: Path) -> None:
    """config.py is the only module permitted to read the environment."""
    offenders: list[str] = []
    for path in (project_root / "src").rglob("*.py"):
        if path.name == "config.py":
            continue
        body = path.read_text(encoding="utf-8")
        if "os.environ" in body or "os.getenv" in body:
            offenders.append(str(path.relative_to(project_root)))
    assert not offenders, f"environment read outside config.py: {offenders}"


# ── caching behaviour ──────────────────────────────────────────────────────


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_settings_json_roundtrip_is_loggable() -> None:
    """Whatever we serialise for logs must be valid JSON and secret-free."""
    s = Settings(_env_file=None, anthropic_api_key=SecretStr(SECRET))  # type: ignore[call-arg]
    parsed = json.loads(s.model_dump_json())
    assert parsed["llm_model"] == "claude-opus-5"
    assert SECRET not in json.dumps(parsed)
