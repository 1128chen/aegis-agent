"""HuggingFace cache helpers (stdlib only, never touches the network)."""
import os


def hub_cache_root() -> str:
    return (
        os.environ.get("HF_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    )


def cached_model_path(model_id: str):
    """Return the newest local snapshot dir for a model id, or None if absent."""
    key = str(model_id).strip("/").replace("/", "--")
    snapshots = os.path.join(hub_cache_root(), "models--" + key, "snapshots")
    try:
        names = sorted(os.listdir(snapshots))
    except OSError:
        return None
    if not names:
        return None
    return os.path.join(snapshots, names[-1])
