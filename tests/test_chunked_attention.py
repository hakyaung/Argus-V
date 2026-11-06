import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "argus_hook"))

from transformers.models.gpt_oss import modeling_gpt_oss as m  # noqa: E402

import chunked_attention as ca  # noqa: E402


def _module(heads=8, kv_heads=2):
    return SimpleNamespace(num_key_value_groups=heads // kv_heads, sinks=torch.randn(heads), training=False)


def _causal_mask(q_len, k_len, window=None):
    i = torch.arange(q_len)[:, None] + (k_len - q_len)
    j = torch.arange(k_len)[None, :]
    allowed = j <= i
    if window:
        allowed &= j > i - window
    mask = torch.zeros(q_len, k_len)
    mask[~allowed] = torch.finfo(torch.float32).min
    return mask[None, None]


@pytest.mark.parametrize("q_len,window", [(37, None), (37, 8), (5, None), (1, None)])
def test_chunked_matches_original(q_len, window):
    torch.manual_seed(0)
    mod = _module()
    k_len = q_len
    q = torch.randn(1, 8, q_len, 16)
    k = torch.randn(1, 2, k_len, 16)
    v = torch.randn(1, 2, k_len, 16)
    mask = _causal_mask(q_len, k_len, window)
    expected, _ = m.eager_attention_forward(mod, q, k, v, mask, 0.25)
    got, weights = ca.chunked_eager_attention_forward(mod, q, k, v, mask, 0.25, chunk=4)
    assert got.shape == expected.shape
    torch.testing.assert_close(got, expected, rtol=0, atol=1e-6)


def test_short_input_uses_original_path():
    mod = _module()
    q = torch.randn(1, 8, 3, 16)
    k = torch.randn(1, 2, 3, 16)
    v = torch.randn(1, 2, 3, 16)
    expected, _ = m.eager_attention_forward(mod, q, k, v, None, 0.25)
    got, _ = ca.chunked_eager_attention_forward(mod, q, k, v, None, 0.25, chunk=512)
    torch.testing.assert_close(got, expected, rtol=0, atol=0)


def test_install_is_idempotent_and_can_be_disabled(monkeypatch):
    original = m.eager_attention_forward
    try:
        monkeypatch.setenv("ARGUS_ATTN_CHUNK", "0")
        assert ca.install() is False
        assert m.eager_attention_forward is original
        monkeypatch.delenv("ARGUS_ATTN_CHUNK")
        assert ca.install() is True
        assert ca.install() is True
        assert m.eager_attention_forward is ca.chunked_eager_attention_forward
    finally:
        m.eager_attention_forward = original
        ca._ORIGINAL = None
