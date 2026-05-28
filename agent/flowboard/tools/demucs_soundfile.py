from __future__ import annotations

import sys

import soundfile as sf
import torch
import torchaudio


def _load_with_soundfile(
    uri,
    frame_offset=0,
    num_frames=-1,
    normalize=True,
    channels_first=True,
    format=None,
    buffer_size=4096,
    backend=None,
):
    del normalize, format, buffer_size, backend
    start = max(0, int(frame_offset or 0))
    frames = -1 if num_frames is None or int(num_frames) < 0 else int(num_frames)
    audio, sample_rate = sf.read(
        str(uri),
        start=start,
        frames=frames,
        dtype="float32",
        always_2d=True,
    )
    if audio.shape[1] == 1:
        audio = audio.repeat(2, axis=1)
    tensor = torch.from_numpy(audio)
    if channels_first:
        tensor = tensor.transpose(0, 1).contiguous()
    return tensor, int(sample_rate)


def _save_with_soundfile(
    uri,
    src,
    sample_rate,
    channels_first=True,
    encoding=None,
    bits_per_sample=None,
    **_kwargs,
):
    audio = src.detach().cpu().numpy() if hasattr(src, "detach") else src
    if channels_first and getattr(audio, "ndim", 0) == 2:
        audio = audio.T

    subtype = "PCM_16"
    if bits_per_sample == 24:
        subtype = "PCM_24"
    elif bits_per_sample == 32 and encoding == "PCM_F":
        subtype = "FLOAT"
    elif bits_per_sample == 32:
        subtype = "PCM_32"

    sf.write(str(uri), audio, int(sample_rate), subtype=subtype)


torchaudio.save = _save_with_soundfile
torchaudio.load = _load_with_soundfile

from demucs.separate import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
