# Third-party notices

`huiji/notes.py` 的會議紀錄提示詞、範本與 JSON 結構、介面版面，以及 `huiji/recheck.py` 的短句說話者重新確認規則，改寫自 [Shiyin](https://github.com/juinwei7/Shiyin)：

```
MIT License

Copyright (c) 2026 juinwei7

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## sherpa-onnx

[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 使用 Apache License 2.0，會記使用其 SpeakerEmbeddingExtractor 在本機計算聲紋。
授權全文隨套件收錄，亦可見 [上游 LICENSE](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE)。

## 3D-Speaker ERes2Net 聲紋模型

[3D-Speaker](https://github.com/modelscope/3D-Speaker) 的 ERes2Net base 模型使用 Apache License 2.0。
會記使用 [sherpa-onnx 轉換的 ONNX 模型](https://huggingface.co/csukuangfj/speaker-embedding-models/blob/0743f301363dec56491a490f6d6cbc9d67f9a3bf/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx)，首次使用時下載並驗證 SHA-256，不隨安裝檔散布。
授權全文見 [3D-Speaker LICENSE](https://github.com/modelscope/3D-Speaker/blob/main/LICENSE)。
