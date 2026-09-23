"""验证 /getglb 改为 JSON 后：openapi 可生成、响应结构正确、能反解出 GLB 字节。"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.encoders import jsonable_encoder

import app.api.v1.endpoints.catia as ep
from app.main import app
from app.schemas.catia import GlbPayload
from app.schemas.common import ApiResponse

# 1) openapi 能正常生成（能暴露注解 / 模型定义错误）
schema = app.openapi()
glb_path = next(p for p in schema["paths"] if p.endswith("/getglb"))
print("route:", glb_path)
print("200 schema:", json.dumps(schema["paths"][glb_path]["post"]["responses"]["200"], ensure_ascii=False))

# 2) 用假数据跑一遍端点函数（不碰 CATIA）
ep.catia_service.get_glb = lambda index=1: (
    b"\x00\x01FAKE-GLB-BYTES",
    "Bracket",
    [
        {"id": "Bracket.1", "instanceName": "Bracket.1", "partNumber": "P-001"},
        {"id": "Clip.1", "instanceName": "Clip.1", "partNumber": ""},
    ],
)

payload = jsonable_encoder(ep.get_gltf(index=1))
print("top keys:", list(payload.keys()))
print("data keys:", list(payload["data"].keys()))
print("filename:", payload["data"]["filename"])
print("parts:", json.dumps(payload["data"]["parts"], ensure_ascii=False))
print("glb -> decoded:", base64.b64decode(payload["data"]["glb"]))

# 3) 响应模型校验通过
ApiResponse[GlbPayload].model_validate(payload)
print("response_model validate: OK")

# 4) base64 反解后与原始字节一致
assert base64.b64decode(payload["data"]["glb"]) == b"\x00\x01FAKE-GLB-BYTES"
print("roundtrip: OK")
