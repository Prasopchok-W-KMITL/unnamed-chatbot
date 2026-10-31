# UNNAMED Chatbot

แชตบอตร้านทำผม UNNAMED สำหรับตอบข้อมูลบริการ ราคา โปรโมชั่น และคำแนะนำเบื้องต้นผ่าน LINE ใช้ **FastAPI + LightRAG + OpenRouter** ร่วมกับ Guardrail และการตรวจหลักฐานก่อนตอบ

โครงการรายวิชา **06048308 Intelligent Chatbot Development**  
ผู้จัดทำ **ประสบโชค วัฒนะโอฬาร — 69076043** รับผิดชอบโครงการทั้งหมด

ใช้ **LINE OA ใหม่แยกจากระบบร้านเดิม** ไม่อ่านฐานข้อมูลลูกค้าเดิม และยังไม่รองรับการจอง เลื่อน ยกเลิกคิว หรือชำระเงิน

## ภาพรวมโครงการ

- ฐานความรู้ 23 เอกสาร: บริการปกติ 11 รายการ โปรโมชั่น 5 แพ็กเกจ ข้อมูลร้าน 4 รายการ และแนวทางทั่วไป 3 รายการ
- ถามเป้าหมายและประวัติทำเคมีที่ยังขาด ไม่บังคับถามความยาวผม และเคารพบริการที่ผู้ใช้ปฏิเสธ
- รับภาพทรงผม ตอบรับภาพ แล้วใช้ข้อความคำถามถัดไปประกอบการให้คำตอบ
- โปรไฟล์เข้ารหัสและแยกผู้ใช้ด้วย HMAC อายุเริ่มต้น 30 นาทีหลังบันทึกล่าสุด; สรุปภาพเก็บสูงสุด 5 นาทีหรือจนใช้ตอบสำเร็จ ส่ง “เริ่มใหม่” เพื่อล้างบริบท
- ระบบทำงานเมื่อเครื่องและบริการที่เกี่ยวข้องออนไลน์ ไม่รับประกันความพร้อมตลอด 24 ชั่วโมงหรือความถูกต้องทุกกรณี

| หน้าที่ | โมเดล / บริการที่ใช้ในโครงการ |
| --- | --- |
| ตีความคำถามและเลือกรหัสหลักฐาน | Qwen 3.5 Flash ผ่าน OpenRouter |
| วิเคราะห์ลักษณะทรงผมจากภาพ | Qwen 3.5 Flash ผ่าน OpenRouter |
| สกัดข้อมูลและคำค้นใน LightRAG | `gemma3:4b` ผ่าน Ollama |
| Embedding | `bge-m3:latest` ผ่าน Ollama, 1024 มิติ |
| ตรวจข้อความ | `llama-guard3:1b` ผ่าน Ollama |
| ตรวจภาพก่อน Vision | `omni-moderation-latest` ผ่าน OpenAI โดยตรง |

Qwen เลือกเจตนาและรหัสหลักฐานกลับมาเป็น JSON แอปตรวจ schema และเอกสาร แล้วประกอบคำตอบจากข้อความต้นฉบับ โมเดลไม่มีช่องเขียนคำตอบสุดท้ายอย่างอิสระ การเลือกหัวข้อและการตรวจความปลอดภัยยังอาจผิดพลาดได้

## โครงสร้างไฟล์

```text
app/                    FastAPI, engine, providers, Guard, คิว และความจำ
knowledge/              ข้อมูลร้านและแนวทางทั่วไปใน JSON
scripts/                เริ่มระบบ, warmup, readiness และหยุด process ที่บันทึกไว้
tests/                  ชุดทดสอบอัตโนมัติ
evaluation/             ชุดคำถามสังเคราะห์
postman/                Collection สำหรับทดลอง API ผู้ดูแล
docs/                   เอกสารและแผนภาพประกอบ (บางภาพเป็นรุ่นเก่า)
.env.example            ตัวอย่างการตั้งค่าบอต ไม่มีคีย์จริง
.env.lightrag.example   ตัวอย่างการตั้งค่า LightRAG ไม่มีคีย์จริง
compose.yaml            Docker Compose ของบอตและ LightRAG
requirements.lock       Dependencies สำหรับ runtime
requirements-dev.lock   Dependencies รวมเครื่องมือทดสอบ
TEST_RESULTS.md         ผลทดสอบและข้อจำกัดตามช่วงพัฒนา
```

## เริ่มทดลองแบบ Demo บนเครื่องใหม่

ใช้ **Python 3.12** เปิด PowerShell ที่ root ของ repository โหมด Demo ไม่ต้องใช้คีย์บริการภายนอก Docker หรือ Ollama

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m app.cli init
.\.venv\Scripts\python.exe -m app.cli check
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

`init` สร้าง `.env` พร้อมคีย์ภายในแบบสุ่มและไม่พิมพ์ค่า หากมี `.env` แล้วให้ข้ามขั้นตอนนี้ เพราะระบบจะปฏิเสธการเขียนทับคีย์เดิม ไฟล์ตัวอย่างเริ่มด้วย `BOT_MODE=demo` และ `ENABLE_IMAGES=false`

เปิด [Health](http://127.0.0.1:8000/healthz) แล้วทดลองผ่าน [Postman Collection](postman/UNNAMED.postman_collection.json) โดยตั้ง `base_url=http://127.0.0.1:8000` และ `admin_key` เป็นค่า `BOT_ADMIN_KEY` จาก `.env` ในเครื่อง ไม่ใส่คีย์ลง collection ที่แชร์

`POST /api/chat` ต้องมี header `X-Admin-Key` และ body เช่น:

```json
{"message":"ตัดผมราคาเท่าไหร่","session_id":"demo-user"}
```

ใช้ `session_id` เดิมเพื่อคุยต่อ API นี้เป็นเครื่องมือผู้ดูแล ช่องทางลูกค้าคือ LINE โหมด Demo ใช้กฎและฐานความรู้ในไฟล์ จึงไม่ใช่การทดสอบความเข้าใจ LLM หรือคุณภาพการค้น LightRAG

## สิ่งที่ต้องเปิดสำหรับ Connected Mode

1. Docker Desktop และ Ollama
2. LightRAG: `docker compose --profile rag up -d lightrag` (ครั้งแรกใช้ `--build` ตามขั้นตอนด้านล่าง)
3. Backend: `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`
4. Cloudflare Tunnel: `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 --protocol http2`
5. ตั้ง webhook ของ LINE OA ใหม่เป็น `https://<host-จาก-tunnel>/webhook/line` และ Verify เมื่อ URL เปลี่ยน

ผู้ติดตั้งต้องตั้งค่า credential และดาวน์โหลดโมเดลตามหัวข้อการเชื่อมต่อด้านล่างก่อน `start.ps1` จะ warmup โมเดล Ollama ก่อนเปิด backend

## ก่อนอัปโหลด Git

อัปโหลดโค้ดและไฟล์ตัวอย่างจาก root ของ repository นี้ `.gitignore` ยกเว้น `.env`, `.env.lightrag`, `data/`, `.venv/`, `.cache/`, `.tools/` และ log ส่วน `.env.example` และ `.env.lightrag.example` ให้อยู่ใน Git โดยมีเฉพาะค่าตัวอย่าง

ตรวจรายการไฟล์ก่อน commit ไม่ให้มี API key, LINE token/secret, ฐานข้อมูล หรือภาพส่วนตัวติดไป หากไฟล์ลับเคยถูกติดตามแล้ว `.gitignore` จะไม่ลบออกจากประวัติให้ เครื่องใหม่ต้องสร้าง environment ตั้งค่าคีย์ ดาวน์โหลดโมเดล และนำเข้าฐานความรู้เอง

## การทำงานเมื่อเชื่อมจริง

```mermaid
flowchart LR
    L[LINE OA ใหม่] --> T[Cloudflare Tunnel]
    T --> S[FastAPI ตรวจ signature และ account]
    S --> Q[คิวเข้ารหัส SQLite]
    Q --> W[Worker]
    W --> C{ตรวจความเสี่ยงชัดเจนในเครื่อง}
    C -->|พบ| H[ข้อความช่วยเหลือคงที่]
    C -->|ไม่พบ| P[กรองข้อมูลส่วนตัวและคำสั่งแทรก]
    P --> G[Llama Guard]
    G -->|S11| H
    H --> L
    G -->|safe| R[LightRAG query/data]
    R --> M[OpenRouter เลือก intent และ ID]
    M --> V[ตรวจ ID และประกอบจากต้นฉบับ]
    V --> O[Guard ตรวจคำตอบ]
    O --> L
    W -->|ภาพ| IM[ตรวจไฟล์และ OpenAI Moderation]
    IM --> VI[Qwen Vision]
    VI --> IS[สรุปภาพเข้ารหัสชั่วคราว]
    IS --> ACK[ตอบรับภาพ]
    ACK --> L
    IS -.->|บริบทของคำถามข้อความถัดไป| R
```

แผนภาพแสดงเส้นทางหลักของ Connected Mode ภาพที่อ่านไม่ได้จะไม่ถูกเก็บเป็นบริบท หลังได้รับข้อความรับภาพ ผู้ใช้ส่งคำถามใหม่ผ่าน LINE เพื่อเข้าเส้นทางข้อความตามปกติ คำทักทายและคำสั่ง “เริ่มใหม่” มีเส้นทางกฎแยก

LightRAG ช่วยค้นหลักฐาน โมเดลช่วยตีความคำถามและเลือกหลักฐาน ตัวประกอบคำตอบอ่านราคา/รายละเอียดจากไฟล์ต้นฉบับอีกครั้ง รับเฉพาะ chunk ที่มี source path ที่กำหนดหรือชื่อไฟล์ตรงรายการอนุญาต (LightRAG v1.5.7 ตัด path เหลือชื่อไฟล์) และมี hash พร้อมเนื้อหาต้นฉบับครบตรงเอกสารปัจจุบัน ไม่ใช้ข้อความตอบอิสระหรือกราฟที่โมเดลสรุปมาเป็นราคา

การเลือกหลักฐานอาจยังผิดหัวข้อได้ แม้ข้อความนั้นอยู่ในฐานความรู้ จึงต้องวัดความเกี่ยวข้องและการปฏิเสธคำถามนอกเรื่องด้วยโมเดลจริงด้วย ไม่ถือว่า RAG ป้องกันการตอบผิดได้ทั้งหมด

ข้อความช่วยเหลือใน `app/safety.py` ใช้ข้อความคงที่ที่เตรียมไว้แยกจากข้อมูลร้าน ไม่ให้โมเดลตอบคำปรึกษาสุขภาพอย่างอิสระ และไม่เพิ่มข้อความผู้ใช้ลงโปรไฟล์หรือ LightRAG เส้นทางนี้ยังไม่ใช่ระบบตรวจความเสี่ยงหรือให้คำปรึกษาที่ครอบคลุมทุกกรณี

ตัวตรวจในเครื่องเป็น heuristic มีตัวอย่างปฏิเสธและสำนวนทั่วไปในชุดทดสอบ ส่วนหมวด S11 อ้างอิง [Llama Guard 3-1B model card](https://huggingface.co/meta-llama/Llama-Guard-3-1B/blob/main/README.md) รับเฉพาะ label/รายการหมวดที่ตรงรูปแบบ ผลผิดรูปแบบยังถูกบล็อก พบ Guard พลาดสำนวนอ้อมในการทดสอบจริง จึงไม่รับรองว่าระบบตรวจความเสี่ยงครบทุกกรณี และไม่ใช่ระบบให้คำปรึกษาต่อเนื่อง

### 1. เตรียม credential ใหม่

เตรียม Docker Desktop, Ollama, LINE Messaging API channel ใหม่ และคีย์ OpenRouter ของตนเอง แล้วตั้ง `.env` ในเครื่อง:

| ตัวแปร | ค่า |
| --- | --- |
| `LINE_NEW_CHANNEL_SECRET` | Secret ของ Messaging API channel ใหม่ |
| `LINE_NEW_CHANNEL_ACCESS_TOKEN` | Token ของ channel ใหม่ |
| `LINE_NEW_BOT_USER_ID` | userId ของบอตใหม่ รูปแบบ `U` + hex 32 ตัว จาก bot info |
| `OPENROUTER_API_KEY` | API key ใหม่สำหรับโปรเจกต์นี้ |
| `OPENROUTER_MODEL` | model ID ที่รองรับภาษาไทยและ structured outputs |
| `LIGHTRAG_API_KEY` | key ของ LightRAG instance ใหม่นี้ |
| `GUARD_BASE_URL`, `GUARD_MODEL` | OpenAI-compatible endpoint และ ID ของ Llama Guard ที่โหลดจริง |
| `GUARD_API_KEY` | key ของ guard endpoint หากต้องใช้ |

โปรไฟล์โมเดลที่ใช้ในการพัฒนาคือ Qwen 3.5 Flash ทั้ง planner และ Vision ตั้งค่าใน `.env` ดังนี้:

```dotenv
OPENROUTER_MODEL=qwen/qwen3.5-flash-02-23
OPENROUTER_VISION_MODEL=qwen/qwen3.5-flash-02-23
OPENROUTER_PLANNER_FORMAT=json_schema
OPENROUTER_DISABLE_REASONING=true
OPENROUTER_SEND_TEMPERATURE=true
ALLOW_PAID_MODELS=true
OPENROUTER_ZDR=false
```

โปรไฟล์นี้อนุญาตโมเดลเสียเงิน ผู้ติดตั้งต้องตรวจการรองรับโมเดล/endpoint และตั้งเพดานคีย์ใน OpenRouter เอง โครงการใช้เพดาน $1; แอปไม่ตั้งหรือเพิ่มเพดานบัญชีให้ ไฟล์ตัวอย่างยังเริ่มด้วย `ALLOW_PAID_MODELS=false` และ ZDR=true เพื่อให้ผู้ติดตั้งเลือกนโยบายเอง

แอปส่ง `data_collection=deny`, `require_parameters=true` และค่า ZDR ตาม config หากไม่มี route ที่ตรงเงื่อนไขจะปฏิเสธ ไม่เปลี่ยนโมเดลหรือผ่อนนโยบายเอง ZDR=false ไม่ใช่การรับประกันว่าผู้ให้บริการไม่เก็บข้อมูล ดู [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection)

`OPENROUTER_PLANNER_FORMAT` ค่าเริ่มต้น `json_schema` ใช้ strict JSON Schema จาก endpoint ที่รองรับ ส่วน `json_object` ใช้ JSON mode และส่ง schema เป็นคำสั่งให้โมเดล ยังตรวจ schema ด้วย Pydantic และตรวจ ID/ข้อความต้นฉบับในแอปเหมือนเดิม ผลผิดรูปแบบถูกปฏิเสธ ไม่มีการซ่อม JSON หรือ retry เมื่อ schema ผิด การตั้งค่านี้ใช้กับ planner เท่านั้น Vision ยังใช้ JSON Schema การรองรับขึ้นกับ endpoint; ดู [API parameters](https://openrouter.ai/docs/api/reference/parameters)

Vision ถูกสั่งให้สรุปเฉพาะลักษณะทรงผมที่มองเห็นเป็น enum เพิกเฉยต่อคำสั่งในภาพ และปฏิเสธภาพว่างหรืออ่านไม่ชัด การผ่าน schema และ moderation ยังไม่ยืนยันความถูกต้องของการมองภาพ ผลทดสอบกับภาพสังเคราะห์ในอดีตอยู่ใน [TEST_RESULTS.md](TEST_RESULTS.md)

### 2. เตรียม LightRAG แยก

ดาวน์โหลดโมเดลผ่าน Ollama และสร้างไฟล์ตั้งค่าเฉพาะครั้งแรก:

```powershell
ollama pull gemma3:4b
ollama pull bge-m3
ollama pull llama-guard3:1b
if (-not (Test-Path .env.lightrag)) { Copy-Item .env.lightrag.example .env.lightrag }
```

กำหนด `LIGHTRAG_API_KEY` ให้เป็นค่าลับเดียวกันทั้ง `.env` และ `.env.lightrag` และใช้ค่านี้เข้า WebUI ไม่ใช้คีย์ OpenRouter หรือ OpenAI แทน

ติดตั้งและเปิด Docker Desktop แล้วคัดลอก `.env.lightrag.example` เป็น `.env.lightrag` ใส่ key เดียวกับ `LIGHTRAG_API_KEY` ใน `.env` ตั้ง LLM สำหรับสร้างดัชนี และ multilingual embedding model พร้อม host/key/dimension ตามบริการที่เลือก **embedding เป็นคนละโมเดลกับ chat** และค่าใช้จ่าย/โควตาแยกกัน หากใช้ embedding ในเครื่องให้ตั้ง host ที่ Docker เข้าถึงได้

ห้ามส่ง `.env` ของบอตทั้งไฟล์เข้า LightRAG เพราะบริการนี้ไม่ต้องใช้ LINE token หรือ DATA_KEY ตั้ง `WHITELIST_PATHS=/health` ไว้เพื่อจำกัด endpoint ที่ไม่ต้องใช้ key

ค่าตัวอย่างของ LightRAG ใช้ Ollama ในเครื่อง: `gemma3:4b` สำหรับสกัดข้อมูล/คำค้น และ `bge-m3:latest` สำหรับ embedding ขนาด 1024 มิติ เปิด Ollama และดาวน์โหลดด้วย `ollama pull gemma3:4b` กับ `ollama pull bge-m3` ก่อน Docker ใช้ `http://host.docker.internal:11434` เพื่อเรียก Ollama บน Windows ส่วนกรณีรัน LightRAG บน host โดยตรงให้ใช้ `http://127.0.0.1:11434` ค่าตัวอย่างจำกัด context 8192 และ LLM concurrency 1 เพื่อเริ่มทดสอบกับ VRAM 8 GB; ต้องวัดความเร็วและคุณภาพการสกัดข้อมูลจริง และหลีกเลี่ยงโหลด Guard 8B พร้อมกัน

หลังแก้ `.env.lightrag` ให้รัน `docker compose --profile rag up -d --no-deps --force-recreate lightrag` ในโฟลเดอร์โปรเจกต์เพื่อโหลดค่าใหม่ การกด restart container อย่างเดียวไม่เปลี่ยน environment ตรวจ `/health` แบบส่ง API key ว่าโมเดลถูกต้องก่อนกด Scan/Retry สำหรับเอกสารที่ failed อยู่แล้ว ไม่ต้องลบ Volume การเปลี่ยนนี้มีผลเฉพาะ LightRAG; ตัวบอตยังใช้ OpenRouter ตาม `.env` และต้องทดสอบ route ฟรี, ZDR และ JSON schema แยกกัน

```powershell
docker compose --profile rag up -d --build lightrag
.\.venv\Scripts\python.exe -m app.cli ingest
```

Compose build จาก release `v1.5.7` ของ [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG/tree/v1.5.7) และเก็บดัชนีใน volume เฉพาะโปรเจกต์ คำสั่ง `ingest` ส่งเฉพาะเอกสารสาธารณะที่ยืนยัน/ยังมีผลอยู่ ใช้ `/documents/text` และรอให้เอกสารทั้งหมดมีสถานะ processed ใน LightRAG ก่อนทดสอบ ไม่ถือว่า accepted คือสร้างดัชนีสำเร็จ

แก้ข้อมูลใน `knowledge/*.json` แล้วตรวจ `check`, restart บอต และ `ingest` อีกครั้ง เอกสารที่เปลี่ยนจะได้ source version ใหม่ เอกสารที่เหมือนเดิมยังใช้ source เดิมได้ ตัวตรวจจะไม่รับราคาฉบับเก่า ระวังเปลี่ยน embedding model/dimension หลังสร้างดัชนี: ให้ใช้ดัชนีใหม่แยกแทนการปะปนข้อมูลคนละ embedding

`export` สร้างไฟล์ตรวจทานสาธารณะใน `data/public-export/` แต่ควรใช้ `ingest` เพื่อคง source path ที่บอตคาดหวัง

### 3. เตรียม Llama Guard และเปิด connected

ต้องมี inference server ที่โหลด Llama Guard จริงและตอบ `/v1/chat/completions` ด้วย `safe` หรือ `unsafe` ห้ามใส่ชื่อ chat model ปกติแทน guard ตัว adapter รับเฉพาะ `safe` แบบตรงตัว หาก endpoint ล่ม ตอบ JSON ผิด หรือให้คำอธิบายแทน label จะหยุดตอบข้อมูล

เครื่องนี้ใช้ `GUARD_BASE_URL=http://127.0.0.1:11434/v1`, `GUARD_MODEL=llama-guard3:1b` และ `GUARD_API_KEY=` ว่าง เปิด Docker/Ollama ก่อนเริ่มบอต แล้วใช้ `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1` ซึ่งโหลดโมเดล local ก่อนเปิด uvicorn หรือโหลดแยกด้วย `.\.venv\Scripts\python.exe -X utf8 -u scripts/warmup.py` helper นี้เฉพาะชุด Ollama ในเครื่องตาม README; หากใช้ inference server อื่นให้ตรวจ readiness แล้วเริ่ม uvicorn โดยตรง Cold start อาจเกิน timeout; การโหลดให้ค้างตอนเริ่มไม่รับประกันว่าจะไม่ถูก unload/evict หลัง idle หรือหน่วยความจำไม่พอ

ตั้ง `BOT_MODE=connected` เมื่อ LINE, OpenRouter, LightRAG และ guard พร้อมแล้ว restart backend ด้วย `scripts/start.ps1` หรือคำสั่ง uvicorn ในหัวข้อ Demo การตั้งค่าไม่ครบจะเปิด connected ไม่ได้ ต้องทดสอบ label ภาษาไทยจริงด้วย

เริ่มโดยรันบอตบน host และ LightRAG ใน Docker เพื่อให้ตรวจปัญหาการเชื่อมต่อได้ง่าย หากจะรันบอตใน Docker ด้วย ใช้ `docker compose --profile rag up -d --build`; ค่าใน Compose จะชี้ LightRAG ไป `lightrag:9621` และ guard ไป `host.docker.internal:11434/v1` ตรวจว่า guard server เข้าถึงจาก container ได้ก่อน

### 4. ต่อ webhook ของ OA ใหม่

ใช้ HTTPS tunnel สำหรับสาธิตหรือ container host ที่มี persistent disk แล้วตั้ง webhook **เฉพาะ channel ใหม่** เป็น `https://<host>/webhook/line` ปิด auto-reply/greeting ซ้ำใน OA ใหม่นี้ เปิด webhook และทดสอบ Verify/ส่งข้อความจริง

ติดตั้ง Cloudflared เองแล้วเปิดใน Terminal อีกหน้าต่าง:

```powershell
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 --protocol http2
```

หากวาง binary ใน `.tools` ให้ใช้ `.\.tools\cloudflared.exe` แทน `cloudflared` โฟลเดอร์ `.tools` ไม่ได้อยู่ใน Git ใช้ HTTPS URL ที่ได้เติม `/webhook/line` และอัปเดตใน channel ใหม่เมื่อ URL เปลี่ยน อ้างอิง [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

หยุด backend และ Tunnel ที่เปิดเองด้วย Ctrl+C ส่วน `scripts/stop.ps1` หยุดเฉพาะ process เบื้องหลังที่มีบันทึก `data/runtime/managed-processes.json` ไม่หยุด Docker/Ollama และไม่หยุด Terminal ที่เปิดเองทุกตัว

ไม่ต้องแก้ webhook, sheet, GAS หรือ token ของร้านเดิม ระบบตรวจทั้ง signature ของ raw body, `destination` และ `/v2/bot/info` ของ token ก่อนรับงาน ใช้เฉพาะแชตส่วนตัว ไม่รับข้อความกลุ่ม คิว ack ก่อนค้น RAG/เรียกโมเดล

worker ทำงานครั้งละหนึ่งรายการเพื่อรักษาโควตาต้นแบบ งานหมดอายุใน 55 วินาทีและมี timeout การตอบ จึงต้องวัดความเร็วโมเดลจริง เมื่อคิวเต็มจะตอบ HTTP 503 หากการส่งไป LINE มีผลไม่แน่นอนจะไม่ retry งานนั้นอัตโนมัติ เพื่อหลีกเลี่ยงข้อความซ้ำ อาจทำให้บางข้อความไม่มีคำตอบ; ผู้ใช้ส่งคำถามใหม่ได้ ไม่มีการรับรอง exactly-once

### 5. ตั้งค่ารับภาพ

เลือก `omni-moderation-latest` สำหรับ Guard ภาพ โดยเรียก OpenAI โดยตรง ส่วน Guard ข้อความยังเรียก Llama Guard ใน Ollama ตามเดิม ตั้งใน `.env`:

```dotenv
IMAGE_GUARD_PROVIDER=openai
IMAGE_GUARD_MODEL=omni-moderation-latest
OPENAI_MODERATION_API_KEY=
ENABLE_IMAGES=false
```

ใส่ key ใหม่จากบัญชี OpenAI ลง `OPENAI_MODERATION_API_KEY` ในเครื่อง แยกจาก OpenRouter key และ local guard key ตัวเชื่อมส่งภาพ JPEG ที่ re-encode ใน RAM ไป `https://api.openai.com/v1/moderations` ไม่ส่ง raw LINE identifier, profile, ข้อความลูกค้า หรือ URL ภาพให้บริการไปดาวน์โหลดเอง ตรวจ `flagged` และ boolean ของ categories; ถ้าผลผิดรูปแบบ, ขาดข้อมูล, key/โควตาไม่พร้อม หรือ API ล่มจะหยุดก่อน Vision แม้ flagged=false แต่ category ใดเป็น true ก็ปฏิเสธ ไม่มี fallback ข้าม Guard

Moderation API รับภาพตาม [OpenAI Docs](https://developers.openai.com/api/docs/guides/moderation) และยังมี rate limits ความสามารถตรวจภาพครอบคลุมเฉพาะบางหมวด เช่น sexual, violence และ self-harm หมวด text-only ที่ได้ค่า 0 จาก image-only ไม่ได้ยืนยันว่าเนื้อหานั้นปลอดภัย การตรวจนี้ไม่ใช่ตัวตรวจ PII หรือ prompt injection โดยเฉพาะ จึงยังจำกัด Vision ให้สรุปลักษณะผมเป็น enum และตรวจหลักฐาน RAG ตามเดิม

เมื่อ key, `OPENROUTER_VISION_MODEL` และบริการทั้งหมดพร้อม ให้ทดสอบภาพสังเคราะห์ก่อนตั้ง `ENABLE_IMAGES=true` และ restart ต้องเป็น connected เท่านั้น ไฟล์ตัวอย่างปิดภาพไว้; โครงการเคยเปิดรับภาพและทดสอบบริการจริงตามบันทึกผลทดสอบ การไม่มี key หรือใส่ key รูปแบบ OpenRouter แทนจะเปิดภาพใน config ไม่ได้ และ adapter จะไม่ส่ง key ผิดประเภทไป OpenAI รองรับ image guard เดิมผ่าน `IMAGE_GUARD_PROVIDER=llama_guard` เพื่อความเข้ากันได้ แต่ Llama Guard รุ่นข้อความอย่างเดียวใช้ตรวจภาพไม่ได้

ภาพจาก LINE ถูกจำกัด 5 MB, JPEG/PNG จริง, 16 ล้านพิกเซล และภาพนิ่งหนึ่งเฟรม แปลงภาพใน RAM, ลบ metadata ด้วยการ encode ใหม่ และย่อก่อนส่ง ตรวจด้วย guard ภาพก่อน Vision ผลลัพธ์เป็น enum ของลักษณะทรงผม ไม่รับข้อความอิสระ ไม่อนุมานประวัติทำเคมีหรือสุขภาพจากรูป จากนั้นใช้ flow ถามเพิ่ม/RAG เดิม

ไม่เขียนภาพลงไฟล์หรือเข้า LightRAG แต่ภาพยังถูกส่งไป OpenAI สำหรับ moderation และ OpenRouter สำหรับ Vision หลังผ่านการตรวจ ต้องแจ้งผู้ใช้และตรวจนโยบายบริการก่อนเปิดรับจริง ห้ามใช้รูปหรือสลิปลูกค้าจริงเป็นชุดทดสอบโดยไม่มีสิทธิ์

## ความรู้และข้อมูลส่วนตัว

- `knowledge/store.json`: ข้อมูลร้านและบริการ ที่จอดรถยังไม่ยืนยัน โปรโมชันเก่ายังไม่ยืนยันจึงไม่ถูกนำไปใช้
- `knowledge/advice.json`: แนวทางทั่วไป 3 รายการพร้อมแหล่ง Wella/KERASILK ยังไม่ใช่คำรับรองเทคนิคหรือผลิตภัณฑ์ของร้าน ให้ช่างตรวจทานก่อนใช้กับลูกค้าจริง
- ไม่เก็บ transcript; เก็บ goal/length/history/risk แบบ enum และรายการบริการที่ปฏิเสธ แยกผู้ใช้และเข้ารหัส ใช้ HMAC แทน raw LINE user ID ในโปรไฟล์; ตัวระบุที่จำเป็นอาจอยู่ใน payload คิวเข้ารหัสชั่วคราว
- ข้อความ/reply token/message ID ในคิวถูกเข้ารหัส และล้าง payload หลังเสร็จหรือหมดอายุ ตัวระบุ event แบบ HMAC เก็บเพื่อ dedup ไม่เกิน 24 ชั่วโมง การล้างทำเมื่อมีงานเข้าหรือ worker ทำงาน
- ปิด access log ของ uvicorn ในคำสั่งที่ให้ไว้ ไม่พิมพ์ key/ข้อความ provider error การเข้ารหัสเป็นการป้องกันไฟล์ ไม่ครอบคลุมเครื่องที่ถูกยึดหรือ DATA_KEY ที่รั่ว
- ตัวกรองข้อมูลส่วนตัวเป็น heuristic จับเบอร์ อีเมล เลขบัญชี ฯลฯ ไม่รับรองว่าครอบคลุม PII ทุกแบบ ลดความเสี่ยงเพิ่มเติมโดยไม่เชื่อมข้อมูลลูกค้าเดิมและไม่ส่ง identifier ให้โมเดล
- โควตา Guard, retrieval และ planner/Vision แยกตาม role ค่าเริ่มต้นอยู่ใน `.env.example` โควตาภายในไม่ใช่เครดิตหรือโควตาฝั่งผู้ให้บริการ และไม่ครอบคลุมจำนวน LLM/embedding ภายใน LightRAG การเพิ่มเพดานไม่รีเซ็ตตัวนับเดิม

## หยุดและเปิดระบบครั้งถัดไป

- กด Ctrl+C ใน Terminal ของ backend และ Tunnel ที่เปิดเอง
- หยุด LightRAG ด้วย `docker compose --profile rag stop lightrag` เพื่อคงดัชนีใน volume
- ครั้งถัดไปเปิด Docker Desktop และ Ollama แล้วเริ่ม LightRAG, backend และ Tunnel ตามลำดับ ตรวจ webhook หาก URL เปลี่ยน
- ไม่ต้อง ingest ใหม่หากข้อมูลและดัชนีเดิมยังอยู่ และไม่ใช้ `docker compose down -v` หากต้องการเก็บดัชนี

## ทดสอบและประเมิน

| รูปแบบ | ขอบเขต |
| --- | --- |
| Automated tests | pytest กับ provider จำลอง ตรวจตรรกะ หลักฐาน ความจำ คิว และ error |
| Demo evaluation | คำถามสังเคราะห์ในโหมดกฎ ไม่เรียกโมเดล |
| Connected API | ตัวอย่างคำถาม/ภาพกับบริการจริง ใช้โควตาและอาจมีค่าใช้จ่าย |
| LINE testing | ผู้ทดสอบส่งข้อความ/ภาพและบันทึกคำตอบเทียบเกณฑ์ผ่าน |

ผล pytest รอบที่บันทึกในงานพัฒนาวันที่ 31 ตุลาคม 2569 คือ **393 passed, 1 deprecation warning** ไม่ใช่คะแนนความแม่นยำโมเดลหรืออัตราผ่าน LINE ผล Demo เดิม 64/64 และ Connected API smoke tests เป็นคนละขอบเขต ดู [TEST_RESULTS.md](TEST_RESULTS.md) สำหรับบันทึกตามช่วงพัฒนา โดยบางการตั้งค่าในประวัติเป็นข้อมูลในอดีต ไม่สรุปผล LINE ว่าผ่านครบชุดจากผล API

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.evaluate
```

ชุด pytest ใช้ MockTransport/โมเดลจำลอง ตรวจ API, LINE signature/identity, คิว/expiry/recovery, การแยกผู้ใช้, guard, หลักฐานผิด, provider errors และการ decode ภาพจริง ไม่ใช้ token จริง

`app.evaluate` ใช้คำถามสังเคราะห์ 64 กรณีใน [cases.json](evaluation/cases.json) และโหมด demo โดยค่าเริ่มต้น สร้าง `data/evaluation-demo.json` มีผลตามหมวด หลักฐานที่คาดหวัง false blocks latency และจำนวน provider attempts ผลผ่านโหมดนี้ไม่ได้ยืนยันคุณภาพ LLM/เวกเตอร์

เมื่อเชื่อมจริง ให้เริ่มน้อยกรณีเพื่อดูโควตาและ latency:

```powershell
.\.venv\Scripts\python.exe -m app.evaluate --connected --limit 5 --output data/evaluation-connected.json
```

การประเมิน connected จะเรียกบริการจริงและพัก 10 วินาทีต่อกรณี ใช้ DB ชั่วคราวแยกจากผู้ใช้จริง แต่ใช้ quota ของ provider เดียวกัน หากจะรันครบ 64 กรณีต้องจัดงบและตรวจโควตาแต่ละ role รวมถึงจำนวนเรียกภายใน LightRAG ให้เพียงพอ ห้ามตีความ unavailable ว่าผ่าน ไม่ควรลด privacy guard เพื่อให้คะแนนดีขึ้น

ก่อนเปิดจริงยังต้องตรวจ:

1. โมเดลไทยหลายสำนวน/คำสะกดผิดและคำถามต่อเนื่อง; retrieved chunk ตรงหัวข้อและครบพอ
2. รูปทรงผม รูปไม่เกี่ยวข้อง ภาพไม่ชัด และภาพที่แทรกคำสั่ง ด้วย guard/Vision จริง
3. LINE Verify/ข้อความซ้ำ, timeout, restart และ persistent volumes
4. false blocks ของ Llama Guard ภาษาไทย และการปฏิเสธที่ไม่ควรปิดกั้นข้อมูลสาธารณะ เช่น เบอร์ร้าน

ดู [TEST_RESULTS.md](TEST_RESULTS.md) สำหรับผลที่รันจริงล่าสุดและสิ่งที่ยังไม่ได้ทดสอบ

## รายละเอียดการตรวจระบบและการจัดการข้อผิดพลาด

## ตรวจความพร้อมก่อนทดสอบ LINE

จาก root ของ repository หลังเปิด Ollama, LightRAG, backend และ Tunnel แล้ว รัน:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-ready.ps1
```

คำสั่งนี้อ่านสถานะเท่านั้น ไม่เรียก chat completion, moderation, embedding หรือ LightRAG query ไม่ ingest เอกสาร ไม่ส่ง LINE และไม่แก้ webhook/คีย์/โควตา ตรวจ backend และ revision ของฐานความรู้ โมเดลที่ติดตั้ง/โหลดใน Ollama จำนวนเอกสารที่ประมวลผลใน LightRAG โควตาภายในของวันนี้ และอ่านข้อมูลคีย์ OpenRouter กับบัญชี/webhook ของ LINE ใหม่

- `PASS` คือผ่านเฉพาะการตรวจที่ระบุ ไม่ยืนยันว่าบอตตอบจริงผ่านแล้ว
- `WARN` เช่นโมเดลยังไม่โหลด ให้ warmup ตามขั้นตอนเดิม; key limit เกิน $1 ให้ตรวจเพดานใน OpenRouter เอง คำสั่งนี้ไม่เปลี่ยนเพดาน
- `image_moderation: authentication_not_verified` หมายถึงมีคีย์ใน config แต่ยังไม่ได้ส่งภาพเพื่อพิสูจน์ว่าเรียกใช้ได้ เป็นคำเตือนที่ต้องทดสอบผ่าน LINE ภายหลัง
- `FAIL` ให้แก้สาเหตุก่อน เช่น `backend_restart_required`, `models_missing`, `documents_not_ready`, `authentication_failed`, `budget_exhausted` หรือ `webhook_not_ready`
- Exit code 1 เมื่อมี FAIL; 0 เมื่อไม่มี FAIL ซึ่งอาจยังมี WARN จึงต้องอ่านข้อความประกอบ

หากไม่ต้องการติดต่อ OpenRouter/LINE ใช้ `--local-only` และเพิ่ม `--json` สำหรับผลแบบ JSON:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-ready.ps1 --local-only --json
```

ตัวตรวจนี้ออกแบบสำหรับ backend บน Windows และ Ollama ในเครื่องตามการตั้งค่า README ปัจจุบัน ตรวจเอกสารด้วยจำนวนและสถานะเท่านั้น ไม่พิสูจน์เนื้อหาหรือ hash ในดัชนี และไม่ทดสอบการตอบจริงหรือ URL Tunnel จากภายนอก จึงยังต้อง Verify และทดสอบ LINE เอง คีย์ OpenAI moderation ตรวจเพียงการตั้งค่า ไม่อ้างว่า authentication ผ่าน

## ดู trace ของคำถามและภาพ

หลังอัปเดตโค้ดต้อง restart backend ด้วยวิธีเดิมเพื่อเริ่มใช้ trace ใหม่ ไฟล์อยู่ที่ `data\runtime\diagnostics.jsonl` ของบอตใหม่ เปิดติดตามระหว่างทดสอบได้ด้วย:

```powershell
Get-Content .\data\runtime\diagnostics.jsonl -Tail 30 -Wait
```

แต่ละข้อความ/ภาพมี `request_id` แบบสุ่ม ไม่ใช้ LINE ID หรือรหัสลูกค้ามาสร้าง ID บันทึกเวลาไทยพร้อมเขตเวลา `+07:00` ขั้นที่ทำงาน (`stage`) เวลาที่ใช้ (`duration_ms`) ผล (`outcome`) และรหัสสาเหตุ (`reason`) คำตอบ `/api/chat` มี `request_id` สำหรับจับคู่กับ log ส่วนข้อความใน LINE คงถ้อยคำเดิม ให้ค้นตามเวลาที่ทดสอบแล้วใช้ ID เดียวกันติดตามเหตุการณ์ต่อ

ตัวอย่างสาเหตุ: `rate_limited` คือ provider ตอบ 429, `credit_limit` คือ 402, `budget_day`/`budget_minute` คือโควตาภายในหมด, `timeout` คือหมดเวลารอ, `guard_unsafe` คือถูก Guard ปฏิเสธ, `no_verified_evidence` คือไม่พบเอกสารที่ยืนยันได้, `incomplete_evidence` คือหลักฐานแพ็กเกจไม่ครบ และ `invalid_schema` คือผลโมเดลไม่ตรง schema ไม่พิมพ์คำตอบหรือรายละเอียด error ดิบของ provider ลง trace

`stage_finished` บอกเวลาแต่ละขั้นและ `request_finished` บอกเวลาประมวลผลรวม สำหรับ LINE แยกเวลารอคิวไว้ใน stage `queue` อีกเหตุการณ์หนึ่ง ไฟล์จำกัดประมาณ 1 MB ต่อไฟล์ เก็บไฟล์หลักกับสำรอง 3 ไฟล์ (`.1`–`.3`) โดยไม่เก็บข้อความ รูป โปรไฟล์ user ID, reply token หรือคีย์ใน trace ตั้ง `DIAGNOSTICS_ENABLED=false` ได้หากไม่ต้องการเขียนไฟล์

## การตรวจข้อมูลติดต่อร้านที่เป็นสาธารณะ

เพิ่มบริบทว่าบอตให้ข้อมูลร้านในข้อความที่ส่งให้ input Guard โดยยังเก็บข้อความลูกค้าเต็มประโยคไว้ให้ตรวจ ไม่เปลี่ยนผล unsafe ให้เป็น safe พบการบล็อกผิดกับคำถามรวมเวลาเปิด เบอร์ และแผนที่ใน Llama Guard 3 1B จึงเพิ่มการตรวจสำเนาคำตอบสำหรับข้อมูลติดต่อสาธารณะ

เฉพาะคำตอบ facts ที่อ้าง contact-phone/contact-map/opening-hours เท่านั้น ต้องมีเอกสารค้นคืนตรง canonical ปัจจุบันทุกตัวและคำตอบตรงต้นฉบับทั้งข้อความ จึงแทนเบอร์และ Maps URL ที่ตรงรูปแบบข้อมูลร้านด้วยป้ายข้อมูลสาธารณะในสำเนาส่ง output Guard ตรวจ เนื้อหาส่วนอื่นยังถูกตรวจครบและ Guard ต้องตอบ safe คำตอบจริงที่ส่งให้ลูกค้าไม่เปลี่ยน ไม่มีข้อยกเว้นต่อ unsafe S1/S7 กรณีนี้ และไม่แทนค่าข้อมูลลูกค้าหรือข้อความที่เติมเอง หากหลักฐานไม่ครบ/เก่า/ข้อความถูกแก้ จะตรวจตามเดิมหรือปฏิเสธตามเงื่อนไขหลักฐาน

## การลองใหม่เมื่อ OpenRouter ติดขัด

### เวลาค้น LightRAG และเวลารวมของคำขอ

`RETRIEVAL_TIMEOUT_SECONDS` เริ่มต้น 20 วินาที (ปรับได้ 1–25) เพื่อรองรับขั้นสกัดคำค้นในเครื่องที่บางคำถามใช้เกิน 8 วินาที บอตยังจำกัดเวลาประมวลผลรวมที่ 40 วินาทีเท่าเดิม และงาน LINE ที่รอในคิวจะใช้เวลาตามอายุงานที่เหลือจริง ไม่เริ่มนับ 40 วินาทีใหม่เมื่อเข้า engine

แต่ละคำขอใช้ deadline เดียวกันตั้งแต่ API/worker รวมเวลาของ loading ด้วย การค้นเผื่ออย่างน้อย 2 วินาทีให้ planner พร้อม 4 วินาทีให้ output Guard และ 0.5 วินาทีให้การตรวจ/บันทึกในเครื่อง ส่วน planner ใช้ได้ไม่เกินค่าที่ตั้งหรือเวลาคงเหลือหลังกัน 4.5 วินาทีนี้ หากเวลาไม่พอจะหยุดก่อนจองโควตา/เรียก provider ไม่ส่งคำตอบที่ยังไม่ผ่าน Guard และไม่ retry retrieval ที่ timeout

```dotenv
RETRIEVAL_TIMEOUT_SECONDS=20
```

หลังอัปเดตให้ restart เฉพาะ backend เพื่อโหลดค่าใหม่ ไม่ต้องลบดัชนีหรือเพิ่มโควตา LightRAG การเพิ่มเวลานี้ไม่รับประกันว่าทุกคำถามจะเสร็จภายใน deadline จึงยังต้องดู trace เมื่อมี timeout

### การลองใหม่ของ planner และ Vision

เปิดใช้เป็นค่าเริ่มต้นสำหรับ planner และ Vision เท่านั้น หาก OpenRouter ตอบ HTTP 429 หรือ 503 ก่อนส่ง completion จะลองเพิ่มได้สูงสุด 1 ครั้งโดยใช้โมเดลและนโยบายเดิม ทุกครั้งที่ยิงใหม่ต้องจองโควตา role เดิมเพิ่ม ไม่รีเซ็ตตัวนับ และไม่เปลี่ยนเพดานคีย์ $1

อ่าน `Retry-After` ทั้งรูปจำนวนวินาทีและ HTTP date หากไม่มี header จะรอ 0.5 วินาที หาก header ผิดรูปแบบ ขอให้รอนานกว่า 2 วินาที หรือเวลาไม่พอ จะหยุดและใช้ข้อความระบบไม่พร้อมตามเดิม ไม่ย่นเวลาที่ provider ขอเพื่อยิงซ้ำก่อนกำหนด เวลารอรวมกับทั้งสอง attempt อยู่ใน timeout ของขั้นเดิมและ deadline ทั้งข้อความ 40 วินาที

ไม่ retry HTTP 200 ที่มี error envelope/ผลผิด schema/JSON ผิด, network timeout, 401/402/403 หรือการส่งคำตอบ LINE เพราะไม่ต้องการยิงซ้ำเมื่ออาจประมวลผลไปแล้ว Guard, moderation, retrieval และ LINE loading ไม่ใช้กลไกนี้ การเพิ่มโอกาสตอบสำเร็จไม่รับประกันว่าผู้ให้บริการจะพร้อมในครั้งถัดไป ดูเหตุการณ์ `retry_scheduled`, `retry_attempt`, `retry_skipped` ใน trace

ค่าตัวเลือกใน `.env` ของบอตใหม่ (ถ้าไม่ใส่จะใช้ค่าเริ่มต้นด้านล่าง):

```dotenv
PROVIDER_MAX_RETRIES=1
PROVIDER_RETRY_MAX_WAIT_SECONDS=2
```

ตั้ง `PROVIDER_MAX_RETRIES=0` เพื่อปิด retry; จำนวนเพิ่มสูงสุดถูกจำกัดใน schema ไว้ที่ 1 ครั้ง อ้างอิงชนิดข้อผิดพลาดและ `Retry-After` จาก [เอกสาร OpenRouter](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/errors-and-debugging.mdx)

## แจ้งเมื่อบริบทภาพหมดอายุ

สรุปภาพยังมีอายุเริ่มต้น 5 นาทีเท่าเดิม หากลูกค้าถามโดยอ้างถึงภาพที่หมดอายุจริง เช่น “อยากได้ทรงนี้ ต้องทำบริการอะไร” หลังผ่านตัวกรองและ input Guard แล้ว บอตจะแจ้งให้ส่งรูปใหม่ รอรับภาพ และพิมพ์คำถามตาม ไม่เรียก retrieval/planner หรือใช้สรุปภาพเก่ากับคำถามนี้

คำถามราคาที่ระบุบริการ ข้อมูลร้าน และรายการบริการที่ตอบได้โดยไม่ใช้รูปยังตอบตามหลักฐานได้ ไม่ล้างสถานะหมดอายุเพราะถามเรื่องอื่น คำสั่งเริ่มใหม่ ภาพที่ใช้ตอบสำเร็จ และภาพใหม่ที่แทนภาพเก่า จะล้างสถานะดังกล่าว รวมกรณีภาพใหม่ถูกปฏิเสธหรืออ่านไม่ได้

เพื่อแยก “ภาพหมดอายุ” ออกจาก “ไม่มีภาพ” ระบบเก็บเพียง HMAC owner และเวลาใน `image_expiry_notices` ไม่มีภาพ สรุปภาพ หรือข้อความ เก็บสูงสุด 30 นาทีหลังภาพหมดอายุและจำกัด 1,000 แถว เมื่อเกินช่วงนี้จะไม่อ้างว่ามีภาพเก่าหมดอายุ ตารางถูกสร้างอัตโนมัติเมื่อเริ่ม backend โดยไม่แก้ดัชนี LightRAG

## สถานะกำลังประมวลผลใน LINE

เปิด loading animation ก่อนประมวลผลข้อความ/ภาพในแชตส่วนตัว หลัง webhook รับเหตุการณ์เข้าคิวแล้ว ใช้ Messaging API ของ OA ใหม่นี้เท่านั้น ระยะเวลาเริ่มต้น 40 วินาที และหายเมื่อบอตส่งข้อความกลับ การเรียก loading จำกัดรวม 1 วินาที ไม่ retry และความล้มเหลวไม่หยุดการตอบจริง ทั้งหมดอยู่ใน deadline เดิมของ worker ไม่เรียกสำหรับ event ซ้ำ, rate limit, สื่อไม่รองรับ หรือข้อความช่วยเหลือในเส้นทาง local ที่ตรวจพบความเสี่ยง/ความทุกข์ใจชัดเจน

LINE user ID ที่จำเป็นต่อการแสดง loading อยู่เฉพาะ payload คิวที่เข้ารหัส อายุไม่เกินงานเดิม 55 วินาทีและถูกล้างเมื่อจบงาน ไม่ใส่โปรไฟล์/trace/คำขอ AI ไม่มีการส่ง push message หรือใช้ reply token เพิ่มเพื่อแสดง loading

```dotenv
LINE_LOADING_ENABLED=true
LINE_LOADING_SECONDS=40
```

เลือกเวลาได้ 5–60 วินาที ทีละ 5 วินาที และปิดได้ด้วย `LINE_LOADING_ENABLED=false` ตาม [เอกสาร LINE](https://developers.line.biz/en/docs/messaging-api/use-loading-indicator/) ภาพกำลังประมวลผลแสดงเมื่อผู้ใช้อยู่ในหน้าห้องแชตเท่านั้น หากปิดห้องแชตจะไม่แสดงแจ้งเตือนย้อนหลัง ยังต้องทดสอบการแสดงผลจริงใน LINE เอง

