"""One place the tool talks to a language model.

Three call sites used to import google.genai directly, so switching provider
meant editing each of them and keeping their retry and error handling in step.
They now all come through here, and the provider is chosen by the LLM
environment variable:

    LLM=azure_foundry   AZURE_FOUNDRY_API_KEY, AZURE_ENDPOINT,
                        AZURE_FOUNDRY_MODEL, AZURE_API_VERSION
    LLM=gemini          GEMINI_API_KEY, GEMINI_MODEL

With LLM unset it picks whichever provider has credentials, preferring Azure.

Azure is called over plain HTTP rather than through the openai package so the
tool keeps working without another dependency to install and pin.
"""
from __future__ import annotations

import json
import os
import time
import ssl
import urllib.error
import urllib.request

AZURE = "azure_foundry"
GEMINI = "gemini"


class LLMError(RuntimeError):
    """Transport or API failure. Never raised for a model that simply declined."""


# ────────────────────────────────────────────────────────────── provider

def _norm_endpoint(raw: str) -> str:
    """Tolerate a truncated host.

    The endpoint is pasted by hand often enough that it arrives as
    "...cognitiveservices.azure." with the TLD clipped. A URL cannot end at a
    dot, so completing it is safe and saves an error that reads like a network
    fault rather than a typo.
    """
    e = (raw or "").strip().rstrip("/")
    if e.endswith("cognitiveservices.azure"):
        e += ".com"
    elif e.endswith("cognitiveservices.azure."):
        e += "com"
    return e


def provider() -> str:
    want = (os.environ.get("LLM") or "").strip().lower()
    if want in (AZURE, "azure", "foundry"):
        return AZURE
    if want in (GEMINI, "google"):
        return GEMINI
    if os.environ.get("AZURE_FOUNDRY_API_KEY", "").strip():
        return AZURE
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return GEMINI
    return ""


def available() -> bool:
    p = provider()
    if p == AZURE:
        return bool(os.environ.get("AZURE_FOUNDRY_API_KEY", "").strip()
                    and os.environ.get("AZURE_ENDPOINT", "").strip())
    if p == GEMINI:
        return bool(os.environ.get("GEMINI_API_KEY", "").strip())
    return False


def model_name() -> str:
    p = provider()
    if p == AZURE:
        return os.environ.get("AZURE_FOUNDRY_MODEL") or "gpt-4.1"
    if p == GEMINI:
        return os.environ.get("GEMINI_MODEL") or "gemini-flash-latest"
    return ""


def info() -> dict:
    return {"provider": provider() or "none",
            "model": model_name(),
            "configured": available()}


# ────────────────────────────────────────────────────────────── calling

def complete(prompt: str, *, temperature: float = 0.0,
             max_retries: int = 3, timeout: int = 90) -> str:
    """Send one prompt, return the model's text. Raises LLMError on failure."""
    if not available():
        raise LLMError("no language model is configured (set LLM and its API key)")
    p = provider()
    last = None
    for attempt in range(max_retries):
        try:
            return (_azure(prompt, temperature, timeout) if p == AZURE
                    else _gemini(prompt, temperature))
        except LLMError as e:
            last = e
            # 4xx other than rate-limiting will not fix themselves.
            if "429" not in str(e) and "timed out" not in str(e).lower() \
               and " 5" not in str(e):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise last or LLMError("call failed")


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS, using certifi when the interpreter has no usable CA file.

    Some Python builds ship without a populated trust store, which surfaces as
    CERTIFICATE_VERIFY_FAILED and looks like the endpoint is down. Falling back
    to certifi keeps verification ON — this call carries an API key to a
    production service and must never run unverified.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _azure(prompt: str, temperature: float, timeout: int) -> str:
    endpoint = _norm_endpoint(os.environ.get("AZURE_ENDPOINT", ""))
    model    = model_name()
    version  = os.environ.get("AZURE_API_VERSION") or "2025-01-01-preview"
    url = f"{endpoint}/openai/deployments/{model}/chat/completions?api-version={version}"
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "api-key": os.environ.get("AZURE_FOUNDRY_API_KEY", "").strip(),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"Azure returned {e.code}: {detail}") from None
    except Exception as e:
        raise LLMError(f"Azure call failed: {str(e)[:200]}") from None
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"unexpected Azure response: {str(data)[:200]}") from None


def _gemini(prompt: str, temperature: float) -> str:
    try:
        from google import genai
    except ImportError as e:
        raise LLMError(f"google-genai not installed: {e}") from None
    try:
        client = genai.Client()
        r = client.models.generate_content(model=model_name(), contents=prompt)
        return (r.text or "").strip()
    except Exception as e:
        raise LLMError(f"Gemini call failed: {str(e)[:200]}") from None
