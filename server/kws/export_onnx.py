"""Export chinese-hubert-base cut to its first LAYER layers as ONNX (input: float32 [1, samples] 16 kHz,
already normalised the way the feature extractor does; output: [1, frames, 768]), then check it against torch.

usage: export_onnx.py <out.onnx> [layer=9]
"""
import sys
import numpy as np
import torch
from transformers import AutoModel, AutoFeatureExtractor

NAME = "TencentGameMate/chinese-hubert-base"


class Cut(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.m = model

    def forward(self, x):
        return self.m(x).last_hidden_state


def main(out, layer=9):
    fe = AutoFeatureExtractor.from_pretrained(NAME)
    full = AutoModel.from_pretrained(NAME).eval()
    cut = AutoModel.from_pretrained(NAME, attn_implementation="eager").eval()
    cut.encoder.layers = cut.encoder.layers[:layer]
    print("feature extractor normalize:", fe.do_normalize)
    x = np.random.default_rng(0).standard_normal(16000).astype(np.float32) * 0.1
    inp = fe(x, sampling_rate=16000, return_tensors="pt")["input_values"]
    with torch.no_grad():
        ref = full(inp, output_hidden_states=True).hidden_states[layer][0].numpy()
        got = cut(inp).last_hidden_state[0].numpy()
    print("cut vs full layer", layer, "max abs diff:", float(np.abs(ref - got).max()))
    torch.onnx.export(Cut(cut), (inp,), out, input_names=["audio"], output_names=["feat"],
                      dynamic_axes={"audio": {1: "samples"}, "feat": {1: "frames"}}, opset_version=17, dynamo=False)
    import onnxruntime as ort
    sess = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
    for n in (8000, 16000, 37000):
        xi = fe(np.random.default_rng(n).standard_normal(n).astype(np.float32) * 0.1, sampling_rate=16000,
                return_tensors="pt")["input_values"]
        with torch.no_grad():
            r = cut(xi).last_hidden_state[0].numpy()
        o = sess.run(None, {"audio": xi.numpy()})[0][0]
        print(f"onnx vs torch, {n} samples: frames {o.shape[0]}={r.shape[0]}, max abs diff {np.abs(o - r).max():.2e}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 9)
