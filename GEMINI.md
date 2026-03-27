# AGENTS.md - 揪霸 (Jio Ba) 專案代理協作規範

本文件定義本專案的人類開發者與 AI Coding Agent 協作方式，目標是讓任何接手者都能在最短時間內：
1. 了解系統邏輯。
2. 正確修改功能而不破壞既有流程。
3. 以一致標準交付可測試、可維運的程式碼。

---

## 1. 專案定位

揪霸 (Jio Ba) 是一個 Discord 智慧揪團機器人，核心價值不是「投票」，而是：
1. 以 DM 訪談避免群體從眾。
2. 將自然語言偏好轉為結構化限制。
3. 用 Nash Social Welfare (NSW) 進行候選方案排序。
4. 由主揪做最終裁決，並建立 Discord Scheduled Event。

---

## 2. 現行技術棧 (以 repo 現況為準)

1. 語言: Python 3.10+
2. Discord 框架: py-cord (`discord.Bot`)
3. LLM 與流程編排:
    - `langgraph`
    - `langchain`
    - `langchain-google-genai`
4. 資料庫: MongoDB (`motor`)
5. 其他:
    - `python-dotenv`
    - `google-generativeai`

注意: `main.py` 會檢查 `discord.Bot` 是否存在；若使用錯誤套件 (如非 py-cord 相容版本) 會直接中止。

---

## 3. 目錄與責任分層

1. `main.py`
    - 啟動點。
    - 載入 Cogs: `cogs.db`, `cogs.ai_brain`, `cogs.jio`。
    - 啟動環境變數檢查、limited mode 切換。

2. `cogs/jio.py`
    - Slash command 與 Discord 互動介面。
    - 活動建立 Modal、加入流程、Embed 更新。
    - 活動資訊 4W1H seeds 解析。

3. `cogs/graph_agent.py`
    - LangGraph 訪談流程。
    - Prompt injection 偵測 (`is_prompt_injection`)。
    - 使用者回答抽取、追問、補問、完成判斷。

4. `cogs/matching/nsw_calculator.py`
    - 候選方案評分。
    - 使用者效用函數與 NSW 排序。

5. `cogs/db.py`
    - MongoDB CRUD。
    - 活動、參與者、訪談紀錄讀寫。

6. `tests/`
    - `test_nsw_and_gatekeeper.py`: Prompt injection 與 NSW 核心測試。
    - `api_test.py`: API/整體流程驗證。

---

## 4. 環境需求與啟動

### 4.1 必要環境變數

1. `DISCORD_TOKEN`
2. `GOOGLE_API_KEY`
3. `GOOGLE_API_ENDPOINT`
4. `MONGO_URI`

`main.py` 目前會強制檢查 `GOOGLE_API_KEY`、`GOOGLE_API_ENDPOINT`，缺漏即終止。

### 4.2 本機啟動

```bash
pip install -r requirements.txt
python main.py
```

### 4.3 既有維運方式

專案包含 `ecosystem.config.js`，可用 PM2 管理長駐程序。

---

## 5. 核心流程 (必須維持不破壞)

1. 主揪使用 `/jio` 建立活動。
2. 使用者點擊加入後進入 DM 訪談。
3. 訪談流程經過 Gatekeeper 檢查惡意輸入。
4. 抽取器將回答整理到結構化欄位 (answers/dealbreakers)。
5. 達到完成條件後進入候選方案計算。
6. 產生建議方案，主揪裁決，發布結果。

任何 PR 若調整上述流程，必須附上行為差異說明與測試證據。

---

## 6. NSW 邏輯基準

對使用者 $i$ 與候選方案 $x$，使用效用函數 $u_i(x)$ 評分，若踩中該使用者雷區則施加極小值懲罰。

最佳化目標:

$$
x^* = \arg\max_{x \in X} \prod_{i=1}^{n} u_i(x)
$$

實作上需維持以下原則：
1. Dealbreaker 懲罰優先於一般偏好加分。
2. 排序函數可解釋，輸出需可追蹤。
3. 修改 scoring 時必須同步更新測試案例。

---

## 7. 安全與防護要求

1. 必須保留 prompt injection 防護邏輯。
2. 新增提示詞或工具呼叫時，不可讓使用者覆寫 system/developer 層規則。
3. 對外部 API/工具結果做基本防呆與 JSON 解析失敗處理。
4. 任何可疑輸入，寧可進入保守路徑 (reprompt/block) 也不要直接放行。

---

## 8. 開發規範

1. 以最小改動完成需求，避免無關重構。
2. 優先維持 async 流程一致性，不要引入阻塞式 I/O。
3. 新增參數、欄位、狀態時，需同步檢查：
    - 資料庫 schema 寫入與讀取。
    - DM 流程顯示與回寫。
    - 測試案例。
4. 函式若處理 LLM 回傳，必須對格式不穩定情境具容錯。
5. 錯誤訊息以可診斷為原則，避免只回傳「發生錯誤」。

---

## 9. 測試與驗收

每次修改至少滿足以下條件：
1. 既有測試可執行。
2. 涉及規則改動時，補上對應單元測試。
3. 涉及流程改動時，提供手動驗證步驟。

建議最小驗證清單：
1. Prompt injection 範例可被攔截。
2. 一般正常訊息不會誤判為攻擊。
3. NSW 對踩雷候選方案給出低分。
4. `/jio` 建立活動仍可正常送出。

---

## 10. 常用任務指令

```bash
# 啟動機器人
python main.py

# 執行主要測試
python -m unittest tests/test_nsw_and_gatekeeper.py

# 驗證 LLM 連線
python test_llm_connection.py

# 驗證 Google 搜尋工具整合
python test_google_search.py
```

---

## 11. AI Agent 工作守則

1. 先讀需求，再找對應模組，最後修改。
2. 禁止直接改動無關檔案。
3. 若需求與現有架構衝突，先提出風險與替代方案。
4. 交付訊息需包含：
    - 修改檔案清單
    - 行為變更摘要
    - 已執行測試與結果
    - 尚未驗證的風險

---

## 12. 未來演進方向 (Roadmap)

1. 補齊 Tool Node 與外部地點資料來源 (如 Google Places) 的一致介面。
2. 強化多活動並行訪談的 session 管理。
3. 增加 explainability: 方案分數拆解給主揪查看。
4. 補齊 E2E 測試，涵蓋從 `/jio` 到活動落地。

---

## 13. 已知實作細節

1. py-cord 2.7.1 的 Scheduled Event 參數需使用 `Guild.create_scheduled_event(location=..., privacy_level=discord.ScheduledEventPrivacyLevel.guild_only)`。
2. `discord.EntityType` 與 `discord.PrivacyLevel` 在本環境不可用，勿直接替換 enum 寫法。
3. `AIBrain._process_loop` 透過每個 `event_id` 的 `asyncio.Lock` 序列化寫入，避免並發造成歷史訊息損毀。
4. DM 訊息流程為 `on_message(DM) -> resolve_event_for_dm() -> active_event_id 檢查 -> queue_message`。
5. Dashboard 更新屬手動觸發，不是 DB reactive 模式；修改流程時要明確補呼叫更新。
6. 面試截止取消政策: 必須 `READY + HOST >= min_participants` 才可進入裁決，否則標記 `FAILED_MIN_PARTICIPANTS`。
7. 任何 DM channel 對 guild 的存取都需先做 `hasattr(channel, "guild")` 防護。

---

本文件為專案協作契約。若程式行為與本文衝突，以「實際程式 + 測試」為準，並在同一 PR 同步更新此文件。
