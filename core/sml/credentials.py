# Credential resolution for Smart LM download backends.

import os

from ..config_store import get_config_value


def _clean_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    return token or None


def _resolve_huggingface_token(
    explicit_token: str | None,
) -> tuple[str | None, str]:
    token = _clean_token(explicit_token)
    if token:
        return token, "explicit"

    token = _clean_token(os.environ.get("HF_TOKEN"))
    if token:
        return token, "environment"

    # Preserve the legacy environment variable accepted by earlier Eclipse releases.
    token = _clean_token(os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    if token:
        return token, "legacy_environment"

    try:
        from huggingface_hub import get_token  # type: ignore

        token = _clean_token(get_token())
        if token:
            return token, "huggingface_login"
    except ImportError:
        pass

    token = _clean_token(get_config_value("hf_token", ""))
    if token:
        return token, "eclipse_config"
    return None, "none"


def _resolve_modelscope_token(
    explicit_token: str | None,
) -> tuple[str | None, str]:
    token = _clean_token(explicit_token)
    if token:
        return token, "explicit"

    token = _clean_token(os.environ.get("MODELSCOPE_API_TOKEN"))
    if token:
        return token, "environment"

    token = _clean_token(get_config_value("modelscope_token", ""))
    if token:
        return token, "eclipse_config"
    return None, "none"


def resolve_auth_token(
    source: str = "huggingface",
    explicit_token: str | None = None,
) -> str | None:
    token, _source = resolve_auth_token_with_source(source, explicit_token)
    return token


def resolve_auth_token_with_source(
    source: str = "huggingface",
    explicit_token: str | None = None,
) -> tuple[str | None, str]:
    normalized_source = source.strip().lower()
    if normalized_source == "modelscope":
        return _resolve_modelscope_token(explicit_token)
    return _resolve_huggingface_token(explicit_token)


def get_auth_token_status(source: str = "huggingface") -> tuple[bool, str]:
    token, token_source = resolve_auth_token_with_source(source)
    return token is not None, token_source
