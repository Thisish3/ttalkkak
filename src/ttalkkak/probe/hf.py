import json

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import ModelInfo


def model_info_from(
    model_id: str, config: dict, file_sizes: dict[str, int]
) -> ModelInfo:
    heads = config["num_attention_heads"]
    quant = (config.get("quantization_config") or {}).get("quant_method")
    return ModelInfo(
        model_id=model_id,
        family=config["model_type"],
        num_layers=config["num_hidden_layers"],
        num_kv_heads=config.get("num_key_value_heads", heads),
        head_dim=config.get("head_dim") or config["hidden_size"] // heads,
        dtype=str(config.get("torch_dtype") or config.get("dtype") or "bfloat16"),
        quantization=quant,
        max_position=config.get("max_position_embeddings", 0),
        weight_bytes=sum(
            v for k, v in file_sizes.items() if k.endswith(".safetensors")
        ),
    )


def fetch_model_info(model_id: str, api: HfApi | None = None) -> ModelInfo:
    api = api or HfApi()
    try:
        info = api.model_info(model_id, files_metadata=True)
        path = hf_hub_download(model_id, "config.json")
    except (GatedRepoError, RepositoryNotFoundError) as err:
        raise TtalkkakError(
            f"모델 접근 불가(401/404): {model_id}. "
            "HF_TOKEN과 모델 접근 권한을 확인하세요.",
            exit_code=2,
        ) from err
    sizes = {s.rfilename: s.size or 0 for s in info.siblings or []}
    with open(path, encoding="utf-8") as f:
        return model_info_from(model_id, json.load(f), sizes)
