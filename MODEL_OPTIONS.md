# ตัวเลือกโมเดลและสถานะใช้งาน

อัปเดต 17 ตุลาคม 2026: ผู้ใช้เลือก Qwen 3.5 Flash แบบเสียเงินทั้งข้อความและภาพ ทดสอบจริงก่อนเปิดใช้แล้ว คีย์ OpenRouter มีเพดาน $1 ตามที่ผู้ใช้ตั้งเอง ไม่ได้แก้เพดานนี้ในโค้ด

## โปรไฟล์ที่เปิดใช้

```dotenv
ALLOW_PAID_MODELS=true
OPENROUTER_MODEL=qwen/qwen3.5-flash-02-23
OPENROUTER_VISION_MODEL=qwen/qwen3.5-flash-02-23
OPENROUTER_PLANNER_FORMAT=json_schema
OPENROUTER_DISABLE_REASONING=true
OPENROUTER_SEND_TEMPERATURE=true
OPENROUTER_ZDR=false
```

ส่ง temperature=0, provider.data_collection=deny และ require_parameters=true ตามเดิม ไม่ fallback ไปโมเดลอื่น ไม่เปลี่ยน credentials ของ OpenAI moderation/LINE/LightRAG/Ollama

ราคาที่ endpoint Alibaba ประกาศ ณ วันที่ตรวจ: input $0.065 / output $0.26 ต่อหนึ่งล้าน tokens มี text/image/video input และ JSON Schema โดยต้องวัดความถูกต้องภาษาไทย/ภาพจริงเพิ่มก่อนสรุปคุณภาพ [Endpoint metadata](https://openrouter.ai/api/v1/models/qwen/qwen3.5-flash-02-23/endpoints)

ตัวอย่างข้อความ 5,000 input + 300 output tokens คิดเป็น $0.000403 ต่อการเรียก ไม่รวมภาพ/reasoning/ค่าธรรมเนียมอื่น ไม่รับรองจำนวนข้อความต่อวงเงินจริง

## ขอบเขตผลทดสอบ

ราคา 700 บาท, รายการบริการครบ 16 IDs, โปรโมชั่นครบ 5 IDs, คำถามแนะนำแล้วตอบประวัติเคมี, ภาพว่างปฏิเสธ และภาพวาดหัวคนสังเคราะห์พร้อมข้อความถัดไปผ่าน actual Guard/LightRAG/OpenRouter ตาม TEST_RESULTS.md ภาพทดสอบไม่ใช่ภาพลูกค้าจริง

Guard ข้อความยังเป็น Ollama llama-guard3:1b และภาพใช้ OpenAI omni-moderation-latest ส่วน LightRAG ใช้ gemma3:4b + bge-m3 ในเครื่อง การเปลี่ยน planner/Vision ไม่ต้องสร้างกราฟใหม่

## ลิมิต

โมเดล planner/Vision จำกัด 200 ครั้ง/วันต่อบทบาท, 15 ครั้ง/นาที; retrieval 1000/วัน 30/นาที; Guard 1000/วัน 30/นาทีต่อบทบาท คง counter เดิม ไม่รับรองโควตาหรือความพร้อมฝั่ง provider โมเดลเสียเงินยังอาจเจอ 429 ชั่วคราวได้

Dots :free เดิมถูก OpenRouter ปฏิเสธด้วย free-models-per-day หลังใช้ครบ 50 ครั้ง/วัน แม้บัญชีมีเครดิต $5 อยู่ การเปิดโปรไฟล์ paid ทำให้คำขอใหม่ใช้เครดิตตาม token และอยู่ใต้เพดานคีย์ $1

## ตัวเลือกอื่นที่ยังไม่ได้เปิดใช้

| Model ID | Input / Output ต่อ 1M tokens | หมายเหตุ |
| --- | --- | --- |
| openai/gpt-6-luna | $0.10 / $0.50 | ต้องละ temperature; ยังไม่ได้เรียก paid รุ่นนี้ |
| google/gemini-3.1-flash-lite | $0.25 / $1.50 | Standard rate; ยังไม่ได้เรียก paid รุ่นนี้ |

ราคา/ความสามารถตรวจจาก metadata วันที่ 17 ตุลาคม 2026 ไม่รับรองผลบนบอตจนกว่าจะทดสอบจริง ห้ามลดนโยบายข้อมูลเพื่อให้ route ผ่าน
