# 揪霸 (Jio-Ba)

以 AI 為核心的 Discord 揪團機器人，透過私訊逐題訪談、主揪裁決流程與活動公告，降低多人協調成本。

[English README](./README.md)

## 專案能做什麼

Jio-Ba 會幫主揪完成整個活動流程：

1. 在伺服器用 `/jio` 建立活動。
2. 參與者點按鈕加入。
3. Bot 在 DM 一對一逐題訪談。
4. 偏題或高風險回覆進入 warning 與 ON_HOLD。
5. 主揪在 UI 裁決 CONTINUE 或 KICK。
6. 匯總後輸出候選方案並定案公告。

## 主要功能

- `/jio` 僅限伺服器使用（不允許在 DM 發起）。
- 建立活動時有簡單 loading 動畫，完成後替換為主畫面卡片。
- 支援報名截止、面試截止、最低成團人數。
- 可根據活動描述與 seeds 產生訪談題目。
- 同活動可同時訪談多位參與者。
- 同一參與者訊息會 debounce 與序列化，避免洗訊與狀態衝突。
- 模糊回答可跨回合累積（draft），減少重複追問死循環。
- warning 達門檻後進入 ON_HOLD，等待主揪裁決。
- 參與者有 Confirm/Edit 最終確認流程。
- 頻道儀表板即時更新參與者狀態。
- 可匯出活動狀態 JSON。

## 目前指令

- `/jio`
   發起活動。
- `/verdict`
   開啟 ON_HOLD 裁決介面。
- `/export_status`
   匯出活動狀態 JSON。

## 流程概覽

1. 主揪在伺服器執行 `/jio`。
2. Bot 顯示建立表單，並發出活動卡（Join + 管理選單）。
3. 參與者加入後進入面試狀態。
4. Bot 於 DM 先送題目總覽，再逐題訪談。
5. 狀態在 `PENDING`、`INTERVIEWING`、`READY`、`ON_HOLD`、`KICKED` 間轉移。
6. ON_HOLD 由主揪裁決繼續或移出。
7. 產生候選方案後由主揪定案與公告。

## 架構與模組

- `main.py`
   啟動機器人、檢查環境變數、載入 cogs。
- `cogs/jio.py`
   Slash Commands、Discord UI、活動儀表板、流程編排。
- `cogs/ai_brain.py`
   佇列、debounce、呼叫 LangGraph。
- `cogs/graph_agent.py`
   訪談圖節點：gatekeeper、extractor、reprompt、finalize、hold、malicious。
- `cogs/db.py`
   MongoDB CRUD 與狀態遷移。
- `cogs/matching/nsw_calculator.py`
   候選方案評分與排序。

## 資料重點

- Event 會儲存活動資訊、訪談題組、參與者清單、warning policy、workflow 狀態。
- Participant interview 會儲存：
   `current_question_id`、`answers`、`draft_answers`、`revision_count`、`confirmed`、`completed`。

## 環境需求

- Python `>=3.10,<3.11`
- MongoDB
- Discord Bot Token
- Google API Key（Gemini）

## 環境變數

請在 `.env` 設定：

```env
DISCORD_TOKEN=你的_discord_token
MONGO_URI=你的_mongodb_uri
GOOGLE_API_KEY=你的_google_api_key
GOOGLE_API_ENDPOINT=https://generativelanguage.googleapis.com
```

可選：

```env
GEMINI_MODEL_NAME=gemini-2.0-flash
```

## 本機啟動

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## PM2 部署

專案內含 `ecosystem.config.js`：

```bash
pm2 start ecosystem.config.js
pm2 logs
pm2 save
```

## 運維備註

- 需開啟 Message Content Intent 才能完整使用 DM 訪談能力。
- 權限不足時，啟動流程可能切換到 limited mode。
- 為降低跨活動混線，系統目前限制每位使用者單一有效面試上下文。

## 測試與工具腳本

倉庫內可用工具：

- `test_llm_connection.py`
- `test_google_search.py`
- `verify_agent.py`
- `show_cost.py`
- `inspect_genai.py`

## 授權

若專案包含授權檔，請以該檔案為準。

