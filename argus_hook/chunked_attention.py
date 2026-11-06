"""Query-chunked eager attention for gpt-oss (memory fix for long prefixes).

gpt-oss in transformers supports only eager attention on this GPU (no SDPA; the flash kernels need Hopper).
Eager attention builds the full [heads, T, T] score matrix plus a sink-concatenated copy and the softmax
output: at T = 5,000 that is ~3.2 GB per copy, which runs out of the RTX 4090's memory next to the 13 GB of
weights (harness v2 prompts start at ~2,600 tokens and grow ~300 per step).

``chunked_eager_attention_forward`` computes the same thing in blocks of query rows. Softmax is row-wise and
each row sees the full key range and its own mask row, so every output row is computed with the same formula
as the original. Peak memory becomes ``heads x chunk x T`` instead of ``heads x T x T``. Decoding (one query
row) and prefixes up to ``chunk`` tokens take the original code path unchanged.

Use ``install()`` once after the model is loaded; it replaces the module-level ``eager_attention_forward``
that ``GptOssAttention.forward`` looks up on every call.
"""

from __future__ import annotations

import os

import torch
from torch import nn
from torch.nn import functional as F

DEFAULT_CHUNK = 512
_ORIGINAL = None


def _rows(module, query, key_states, value_states, attention_mask, scaling, dropout):
    # Same steps as transformers' gpt_oss eager_attention_forward, for a block of query rows.
    attn_weights = torch.matmul(query, key_states.transpose(2, 3)) * scaling
    if attention_mask is not None:
        attn_weights = attn_weights + attention_mask
    sinks = module.sinks.reshape(1, -1, 1, 1).expand(query.shape[0], -1, query.shape[-2], -1)
    combined_logits = torch.cat([attn_weights, sinks], dim=-1)
    combined_logits = combined_logits - combined_logits.max(dim=-1, keepdim=True).values
    probs = F.softmax(combined_logits, dim=-1, dtype=combined_logits.dtype)
    scores = probs[..., :-1]
    attn_weights = nn.functional.dropout(scores, p=dropout, training=module.training).to(value_states.dtype)
    return torch.matmul(attn_weights, value_states)


def chunked_eager_attention_forward(
    module: nn.Module,
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    attention_mask: torch.Tensor | None,
    scaling: float,
    dropout: float | int = 0.0,
    chunk: int | None = None,
    **kwargs,
):
    chunk = chunk or int(os.environ.get("ARGUS_ATTN_CHUNK", DEFAULT_CHUNK))
    q_len = query.shape[-2]
    if q_len <= chunk:
        return _ORIGINAL(module, query, key, value, attention_mask, scaling, dropout, **kwargs) if _ORIGINAL \
            else _full(module, query, key, value, attention_mask, scaling, dropout)
    from transformers.models.gpt_oss.modeling_gpt_oss import repeat_kv

    key_states = repeat_kv(key, module.num_key_value_groups)
    value_states = repeat_kv(value, module.num_key_value_groups)
    outputs = []
    for start in range(0, q_len, chunk):
        stop = min(start + chunk, q_len)
        mask = None
        if attention_mask is not None:
            mask = attention_mask[:, :, start:stop, :] if attention_mask.shape[-2] == q_len else attention_mask
        outputs.append(_rows(module, query[:, :, start:stop, :], key_states, value_states, mask, scaling, dropout))
    attn_output = torch.cat(outputs, dim=2).transpose(1, 2).contiguous()
    # Full attention weights are never materialised (that is the point); no caller here uses them.
    return attn_output, None


def _full(module, query, key, value, attention_mask, scaling, dropout):
    from transformers.models.gpt_oss.modeling_gpt_oss import repeat_kv

    key_states = repeat_kv(key, module.num_key_value_groups)
    value_states = repeat_kv(value, module.num_key_value_groups)
    out = _rows(module, query, key_states, value_states, attention_mask, scaling, dropout)
    return out.transpose(1, 2).contiguous(), None


def install() -> bool:
    """Swap gpt_oss eager attention for the chunked version. Idempotent; ARGUS_ATTN_CHUNK=0 disables."""
    global _ORIGINAL
    if os.environ.get("ARGUS_ATTN_CHUNK", "") == "0":
        return False
    from transformers.models.gpt_oss import modeling_gpt_oss as m

    if m.eager_attention_forward is chunked_eager_attention_forward:
        return True
    _ORIGINAL = m.eager_attention_forward
    m.eager_attention_forward = chunked_eager_attention_forward
    return True
